# Persona Runtime Cutover B1 — runtime 消费协议 + `compile_persona_runtime()` API + `[persona.v2]` 配置段

> 状态：2026-05-24 立项；本文是 dry-run → runtime 切流序列的第一步（B1），仅落地**协议层 + 配置层 + runtime 入口 API 骨架**，不真正切流。
>
> 上游：[persona-source-importer-go-live-readiness.md](./persona-source-importer-go-live-readiness.md)、[persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)。
>
> 旁支证据：
> - [persona-s12-parity-audit-execution.md](./persona-s12-parity-audit-execution.md)
> - [persona-source-importer-acard-execution.md](./persona-source-importer-acard-execution.md)

---

## 0. 范围与护栏

**In scope（B1 本期落地）**

1. `[persona.v2]` 配置段加入 `BotConfig`，4 个 feature flag 全部默认 off（`runtime_consume=false` / `shadow_compare=false` / `runtime_groups=[]` / `fallback_on_compile_error=true`）
2. `services/persona/compiler.py` 新增 `compile_persona_runtime()`，与 `compile_persona_dry_run()` 共享主体，差别仅在 `mode` 标记 + 错误时是否 raise
3. `_pending_freeze/<id>/` runtime 消费协议设计落字（schema 版本号 / 必需文件清单 / 校验顺序 / 错误分类）
4. `services/persona/runtime.py` 新增 thin wrapper：`load_pending_freeze(persona_id) -> PersonaRuntimeBundle`，flag off 时不被调用
5. `BotConfig.persona_v2` 字段 + `config.toml` `[persona.v2]` 段示例 + `tests/test_persona_runtime_config.py` 默认值回归

**Out of scope（B1 不动）**

- `PromptBuilder.build_static()` / `_build_group_profile_block()` 任何拼接逻辑（B3 起）
- Shadow compare 双算实现（B2）
- `LLMClient.chat()` 任何注入路径
- `services/persona/runtime.py` 实际 prompt block 注入（仅留 stub + 类型骨架）
- Admin SPA Runtime 切换面板（B6）
- 监控埋点 counter 实现（B2 起）
- `_pending_freeze/` 文件本身的产出方式（沿用 A 档已有的 admin SPA Pending Freeze 入口，本文不改）

**护栏**

- D1：所有新增 site 必须 grep 同模式确认无第二处实现；写入 commit body
- D2：`compile_persona_runtime()` 必须有 `pytest.raises(asyncio.CancelledError)` 回归（即使是 sync 也要包一个 `asyncio.wait_for` 测 cancel 行为）
- D3：本文是 B1 的 D3 迁移清单
- D5：targeted 跑 `tests/test_persona_compiler.py` / `tests/test_persona_runtime_config.py` / `tests/test_persona_runtime_loader.py`；全量 pytest 前 `pkill -9 -f pytest`
- D6：B1 不触前端；不需要 `npm run build`，不需要 docker rebuild
- D7：commit 前 `git stash list && git status -uno`；只 stage `services/persona/` / `kernel/config.py` / `tests/test_persona_*` / `config.toml.example`（不存在则跳过）/ 本文

---

## 1. 子任务编号与依赖

| 编号 | 任务 | 依赖 | 关键产物 |
|---|---|---|---|
| B1.1 | `[persona.v2]` 配置段 + `BotConfig` 字段 | — | `kernel/config.py::PersonaV2Config`；默认值回归测试 |
| B1.2 | `_pending_freeze/` runtime 消费协议（设计落字） | — | 本文 §3；`services/persona/runtime.py` 顶部 docstring |
| B1.3 | `compile_persona_runtime()` API | B1.2 | `services/persona/compiler.py::compile_persona_runtime`；`tests/test_persona_compiler.py` 新增 runtime mode 用例 |
| B1.4 | `services/persona/runtime.py` thin loader | B1.2 + B1.3 | `load_pending_freeze()` + `PersonaRuntimeBundle` dataclass；`tests/test_persona_runtime_loader.py` |
| B1.5 | 文档收口（本文 §6 状态表回填 + maintenance-log） | B1.1 ~ B1.4 | `maintenance-log.md` + `docs/migrations/persona-v2-importer.md` 旧→新行 |

B1.1 / B1.2 可并行；B1.3 依赖 B1.2 确定的 schema；B1.4 依赖 B1.2 + B1.3。

---

## 2. B1.1 — `[persona.v2]` 配置段 + `BotConfig` 字段

### 2.1 设计

**配置类（kernel/config.py 新增）**

```python
class PersonaV2Config(BaseModel):
    """Persona Source Importer v2 runtime cutover flags.

    All flags default to off; runtime behavior remains v1 until cutover begins.
    """

    runtime_consume: bool = False
    """Whether runtime consumes _pending_freeze/<id>/ instead of v1 PromptBuilder.

    When False (default), v1 paths are unchanged. When True, runtime loads v2
    draft and feeds prompt blocks to PromptBuilder via persona_v2_blocks
    parameter (added in B3).
    """

    runtime_groups: list[str] = []
    """Group ID whitelist for v2 runtime consumption.

    Empty list means: when runtime_consume=True, only DM uses v2 (no group).
    Non-empty list: v2 is enabled for listed groups + DM only.
    """

    shadow_compare: bool = False
    """Whether runtime double-computes v1+v2 prompt blocks and logs diffs.

    No LLM is sent twice; diff is computed locally and written to
    storage/persona_shadow_diff.log. Independent of runtime_consume.
    """

    fallback_on_compile_error: bool = True
    """Whether runtime falls back to v1 when v2 compile fails.

    Default True; should remain True until v1 path is removed in B6.
    """

    persona_id: str = "default"
    """Which persona to load from config/persona/<id>/_pending_freeze/.

    Single-persona deployment uses 'default'. Multi-persona deployment is
    out of scope for B1; will be revisited in B3+.
    """
```

**挂载点**

`BotConfig` 新增字段（紧邻 `backup`）：

```python
backup: BackupConfig = Field(default_factory=BackupConfig)
persona_v2: PersonaV2Config = PersonaV2Config()
```

**`config.toml` 段示例**

```toml
[persona.v2]
runtime_consume = false
runtime_groups = []
shadow_compare = false
fallback_on_compile_error = true
persona_id = "default"
```

### 2.2 风险

- `config.toml` 已经在用户机器上运行，新增段必须默认 off 且保持向后兼容（缺段时走默认值即可，Pydantic 默认行为）
- `persona_v2.runtime_groups` 存的是 string ID，与 `BotConfig.group.overrides` 的 key 类型对齐（都是 str 形式 QQ 群号）
- 不暴露 env / CLI 映射；这些 flag 必须走 `config.toml` 让 D7 生效（`git stash list && git status -uno`）

### 2.3 验收

- 新增 `tests/test_persona_runtime_config.py`：
  - `test_default_persona_v2_flags_all_off`：`BotConfig()` 默认 `runtime_consume=False` / `shadow_compare=False` / `runtime_groups=[]` / `fallback_on_compile_error=True`
  - `test_persona_v2_from_dict`：从 dict 构造覆写后字段正确
  - `test_persona_v2_runtime_groups_must_be_strings`：传入 int 时 Pydantic 自动转字符串或拒绝（按现有 group.overrides 的行为对齐）
- `uv run pytest tests/test_persona_runtime_config.py -q` 全绿
- `uv run ruff check kernel/config.py tests/test_persona_runtime_config.py` clean
- `uv run pytest tests/test_config.py -q` 不回归

### 2.4 回滚

revert B1.1 commit；`BotConfig` 没有任何下游消费方读 `persona_v2`（B1.4 才接），revert 安全。

---

## 3. B1.2 — `_pending_freeze/` runtime 消费协议

### 3.1 schema 版本号

`_pending_freeze/<id>/_persona_runtime.json` 顶层字段：

```json
{
  "schema_version": "1.0",
  "persona_id": "default",
  "frozen_at": "2026-05-24T12:34:56Z",
  "source_sha256": "...",
  "compile_signature": "..."
}
```

`schema_version`：MAJOR.MINOR；MAJOR 不一致 runtime 拒载 + fallback；MINOR 不一致 warn 不阻断。

`source_sha256`：source.frozen.md 内容 sha256；runtime 在每次启动 + 每小时 ping 时复算，不一致 warn（说明文件被外部改了）。

`compile_signature`：本次 compile 输出的 prompt blocks 拼接的 sha256；用于 B2 shadow compare 锚点。

### 3.2 必需文件清单（runtime 启动校验）

| 文件 | 必需 | 缺失行为 |
|---|---|---|
| `_persona_runtime.json` | ✅ | runtime 拒载 + fallback；error 入 `persona_v2_compile_total{status=error}` |
| `source.frozen.md` | ✅ | 同上 |
| `persona.yaml` | ✅ | 同上 |
| `adapter.yaml` | ✅ | 同上 |
| `guard.yaml` | ✅ | 同上 |
| `runtime.yaml` | ✅ | 同上 |
| `voice.yaml` | ✅ | 同上 |
| `system.yaml` | ✅ | 同上 |
| `eval.yaml` | ⏳ | 缺失 warn 不阻断（v2.1 才用） |
| `state.yaml` | ⏳ | 同上 |
| `thinker.yaml` | ⏳ | 同上 |
| `capabilities.yaml` | ⏳ | 同上 |
| `memory.yaml` | ⏳ | 同上（A 档已写 schema 但 runtime 暂不消费） |
| `trace.yaml` | ⏳ | 同上 |
| `modules/_README.md` | ⏳ | 缺失忽略（首版只是占位） |
| `_import_report.json` | ⏳ | 缺失 warn；runtime 不依赖 report，只用作审计 |

**6 个必需 + 8 个可选**与 [persona-v2-importer.md §1](../migrations/persona-v2-importer.md#1-已完成的文档配置迁移) 第 4 行的 `15 个 .draft/*.yaml partial skeleton` 对齐：必需文件覆盖 v1 prompt block 全部信号源（identity / adapter / guard / runtime / voice / system module 开关）。

### 3.3 校验顺序

```
1. 文件存在性（6 必需 + 1 source.frozen.md + 1 _persona_runtime.json）
2. _persona_runtime.json schema_version MAJOR 检查
3. yaml load 各 6 必需文件
4. compile_persona_runtime() 校验 SystemModule DAG（catalog_contracts / validate_module_graph）
5. _persona_runtime.json.source_sha256 校对当前 source.frozen.md
6. 输出 PersonaRuntimeBundle，runtime 持有
```

任一步失败：
- `fallback_on_compile_error=true`（默认）→ runtime 走 v1，error 入日志 + counter
- `fallback_on_compile_error=false` → runtime 启动失败（仅给 staging 环境压测用）

### 3.4 错误分类

| code | 含义 | 处理 |
|---|---|---|
| `pending_freeze_dir_missing` | `_pending_freeze/<id>/` 不存在 | fallback v1 |
| `runtime_meta_missing` | `_persona_runtime.json` 缺失 | 同上 |
| `schema_version_major_mismatch` | MAJOR 不一致 | 同上 |
| `required_file_missing` | 6 必需文件之一缺失 | 同上 |
| `yaml_parse_error` | yaml 解析失败 | 同上 |
| `system_module_dag_invalid` | DAG 校验失败（环 / 缺 owner） | 同上 |
| `source_sha256_drift` | source.frozen.md 被改 | warn 不阻断（继续走 v2） |
| `optional_file_missing` | 8 可选文件之一缺失 | warn 不阻断 |

---

## 4. B1.3 — `compile_persona_runtime()` API

### 4.1 设计

**与 `compile_persona_dry_run` 的差异**

```
                 dry_run                   runtime
  入口路径       .draft/                   _pending_freeze/
  模式标记       mode="dry_run"            mode="runtime"
  缺文件         返回 ok=false             返回 ok=false（同样不 raise）
  schema 错误    返回 errors[]             返回 errors[]
  调用方         admin parity API + CLI    services/persona/runtime.py loader
  副作用         无                        无（只读）
```

**实现策略**：抽 `_compile_internal(draft_dir, mode)` 私有函数承载主体；`compile_persona_dry_run` / `compile_persona_runtime` 只做路径解析 + mode 标记。两边共享 `_read_draft_files` / `_build_prompt_blocks` / `validate_module_graph`。

### 4.2 接口

```python
def compile_persona_runtime(
    persona_id: str,
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
) -> CompileResult:
    """Compile a frozen persona for runtime consumption.

    Reads from _pending_freeze/<id>/ instead of .draft/. Returns CompileResult
    with mode='runtime' on success or errors=[...] on failure. This function
    is read-only and does not raise.

    Callers (services/persona/runtime.py) decide whether to fallback to v1
    based on BotConfig.persona_v2.fallback_on_compile_error.
    """
```

### 4.3 风险

- `_pending_freeze/<id>/` 内容来自 `.draft/<id>/` 复制（writer.pending_freeze），`compile_persona_runtime` 与 `compile_persona_dry_run` 行为应几乎一致；任何分歧都是设计漏洞
- `mode='runtime'` 字段供 B2 shadow compare 与 B3 PromptBuilder 区分日志 / 监控标签

### 4.4 验收

- `tests/test_persona_compiler.py` 新增 4 条：
  - `test_compile_runtime_happy_path`：从 `_pending_freeze/` 读取并返回 `mode='runtime'`
  - `test_compile_runtime_missing_dir`：dir 不存在返回 `ok=False, errors=['pending freeze not found']`
  - `test_compile_runtime_matches_dry_run_blocks`：同一 source 走 freeze 后两个 mode 输出 `prompt_blocks` 一致
  - `test_compile_runtime_does_not_raise_on_yaml_error`：故意写坏一个 yaml，返回 `ok=False, errors=[...]` 而非抛
- `uv run pytest tests/test_persona_compiler.py -q` 全绿
- `uv run ruff check services/persona/compiler.py tests/test_persona_compiler.py` clean

### 4.5 回滚

revert B1.3 commit；`compile_persona_dry_run` 主体不变（只是抽出 `_compile_internal`），admin parity API 不受影响。

---

## 5. B1.4 — `services/persona/runtime.py` thin loader

### 5.1 设计

新增 `services/persona/runtime.py`：

```python
"""Persona v2 runtime loader (thin wrapper).

Loads _pending_freeze/<id>/ into a PersonaRuntimeBundle. This module is the
single entry point for runtime consumers. It is gated by
BotConfig.persona_v2.runtime_consume; when off, callers must skip loading
entirely (no side effects).

B1 scope: dataclass + load function + targeted tests. No PromptBuilder
integration — that's B3.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .compiler import CompileResult, compile_persona_runtime


@dataclass(frozen=True)
class PersonaRuntimeBundle:
    persona_id: str
    schema_version: str
    source_sha256: str
    compile_result: CompileResult
    pending_freeze_dir: Path

    @property
    def ok(self) -> bool:
        return self.compile_result.ok


def load_pending_freeze(
    persona_id: str,
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
) -> PersonaRuntimeBundle | None:
    """Load _pending_freeze/<id>/ into a PersonaRuntimeBundle.

    Returns None when pending freeze does not exist (caller should fallback to
    v1). Returns a bundle with compile_result.ok=False on compile error
    (caller decides fallback based on BotConfig.persona_v2.fallback_on_compile_error).

    Never raises. Read-only.
    """
    ...
```

### 5.2 风险

- B1 只交付 loader 骨架；调用方 = 0（B3 才接 PromptBuilder）。必须确保**没有任何 runtime 路径无意中调用 `load_pending_freeze`**——通过 grep 同模式扫描确认
- `schema_version` MAJOR mismatch 必须立即降级 fallback；这是切流期间唯一的硬熔断
- `source_sha256` 校对失败仅 warn，不阻断（用户改 source.frozen.md 后必须重 freeze 才生效，但 runtime 不强制）

### 5.3 验收

- 新增 `tests/test_persona_runtime_loader.py`：
  - `test_load_pending_freeze_missing_dir_returns_none`
  - `test_load_pending_freeze_ok_returns_bundle`
  - `test_load_pending_freeze_compile_error_returns_bundle_with_ok_false`
  - `test_load_pending_freeze_schema_version_major_mismatch_returns_bundle_with_ok_false`
  - `test_load_pending_freeze_does_not_raise`：故意删一个必需文件，断言不 raise
- `uv run pytest tests/test_persona_runtime_loader.py -q` 全绿
- D1 同模式扫描：`grep -rn 'load_pending_freeze\|PersonaRuntimeBundle' --include='*.py'` 仅命中 `services/persona/` 和 `tests/`，runtime 路径（`PromptBuilder` / `LLMClient` / `bot.py` / `kernel/`）零命中

### 5.4 回滚

revert B1.4 commit；新文件，无 caller，回滚零风险。

---

## 6. 提交节奏

按 B1.1 → B1.2 → B1.3 → B1.4 → B1.5 顺序产出 5 个独立 commit。**每个 commit 落地后等用户显式说"commit"才执行 `git commit`**（CLAUDE.md "Only commit when user explicitly asks"）。

| commit | 目标 | 文件 |
|---|---|---|
| B1.1 | `[persona.v2]` 配置段 + `BotConfig.persona_v2` 字段 | `kernel/config.py` + `tests/test_persona_runtime_config.py` |
| B1.2 | `_pending_freeze/` runtime 消费协议落字 | 本文 §3（已落） + `services/persona/runtime.py` 顶部 docstring（B1.4 同 commit） |
| B1.3 | `compile_persona_runtime()` API + tests | `services/persona/compiler.py` + `tests/test_persona_compiler.py` |
| B1.4 | `services/persona/runtime.py` thin loader + tests | `services/persona/runtime.py` + `services/persona/__init__.py` + `tests/test_persona_runtime_loader.py` |
| B1.5 | 文档收口（本文 §7 状态表 + maintenance-log + migration doc 追加） | `docs/tracking/persona-runtime-cutover-B1-execution.md` + `maintenance-log.md` + `docs/migrations/persona-v2-importer.md` |

### Commit message 模板

```
feat(persona/runtime): B1.{n} — {short title}

- {bullet 1: behavior}
- {bullet 2: tests}
- {bullet 3: docs / migrations / maintenance-log 链接}

flag-gated; runtime_consume defaults off; PromptBuilder/LLMClient unchanged.
```

---

## 7. 当前状态

| 编号 | 状态 | 落地证据 |
|---|---|---|
| B1.1 | ⏳ 待执行 | `kernel/config.py::PersonaV2Config` + `BotConfig.persona_v2` 字段；`tests/test_persona_runtime_config.py` |
| B1.2 | ✅ 协议设计落字（本文 §3） | runtime 消费协议章节已落；后续在 B1.4 写入 runtime.py docstring |
| B1.3 | ⏳ 待执行 | `services/persona/compiler.py::compile_persona_runtime`；`tests/test_persona_compiler.py` 新增 4 条 |
| B1.4 | ⏳ 待执行 | `services/persona/runtime.py::load_pending_freeze` + `PersonaRuntimeBundle`；`tests/test_persona_runtime_loader.py` |
| B1.5 | ⏳ 待执行 | maintenance-log 当日条目 + migration doc 追加旧→新行 |

---

## 8. 出口标准（B1 完成的判定）

全部满足才能转入 B2：

- [ ] `BotConfig().persona_v2` 默认值全 off（test 锁定）
- [ ] `compile_persona_runtime` 与 `compile_persona_dry_run` 在 frozen 同 source 时输出 `prompt_blocks` byte-equal（test 锁定）
- [ ] `load_pending_freeze` 永不 raise（test 锁定）
- [ ] `grep -rn 'load_pending_freeze\|PersonaRuntimeBundle' --include='*.py'` 在 runtime 路径零命中（D1 同模式扫描）
- [ ] `uv run ruff check services/persona/ kernel/config.py tests/test_persona_*` clean
- [ ] `uv run pytest tests/test_persona_*` 全绿
- [ ] 维护日志当日条目五段齐（变更类型 / 内容 / 影响 / 验证 / 回滚）
- [ ] migration doc §6 末尾追加 B1 旧→新行
