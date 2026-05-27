# Persona Runtime Cutover B3 — 单群灰度切流（fengxiaomeng-v2 → 群 993065015）

> 状态：2026-05-24 立项；本文是 dry-run → runtime 切流序列的第三步（B3），落地**首次真切流**——在群 993065015 一处替换 v1 static block 为 v2 prompt blocks，其他群、私聊、未授权群继续走 v1。
>
> 上游：[persona-runtime-cutover-B2-execution.md](./persona-runtime-cutover-B2-execution.md) / [persona-runtime-cutover-B1-execution.md](./persona-runtime-cutover-B1-execution.md) / [persona-source-importer-go-live-readiness.md](./persona-source-importer-go-live-readiness.md)。
>
> 旁支证据：
> - B2 commits `08761e9` / `da52391` / `9bf5f99`（shadow compare 双算 已验证 v1↔v2 byte-equal 不变量在真实 persona 上仍成立）
> - `config/persona/fengxiaomeng-v2/source.md` 已落地，`_pending_freeze/<id>/` 协议已就绪
> - 旧路径锚点：[services/llm/prompt_builder.py:81](../../services/llm/prompt_builder.py#L81) `build_static`；[services/llm/client.py:2103](../../services/llm/client.py#L2103) `system_blocks = [self._prompt.static_block]`

---

## 0. 范围与护栏

**In scope（B3 本期落地）**

1. `services/persona/runtime_selector.py` 新增 `PersonaRuntimeSelector`：根据 `runtime_consume` + `runtime_groups` + `persona_id` 决定本轮 prompt 是否走 v2
2. `services/llm/prompt_builder.py::PromptBuilder` 新增 `set_runtime_selector()` + `_v2_static_text` 缓存；`build_blocks()` 入参新增 `group_id` 已有，在拼装 system_blocks 第一块时按 selector 决定 v1 vs v2 文本（其他 plugin_static / state_board / plugin_stable / plugin_dynamic 不动）
3. `services/llm/client.py::LLMClient` 把 build_blocks 失败 fallback 中的 `[self._prompt.static_block]` 改为 `self._prompt.resolve_static_block(group_id)`——保证 fallback 路径也尊重 selector
4. `kernel/router.py::_on_connect` 加 selector 装配：从 `_pending_freeze/<persona_id>/` load bundle 一次，喂给 PromptBuilder；flag off / bundle 缺失 / compile 失败时按 `fallback_on_compile_error` 决定继续 v1（warn）还是直接 raise（默认 true，继续 v1）
5. counter 埋点：`persona_v2_runtime_consume_total{result=v2|v1_fallback|v1_default}`（in-memory；与 B2 ShadowCounter 同一文件 / 同模板）
6. 8 条单测覆盖：flag off / runtime_groups 为空 / 群命中 / 群未命中 / 私聊（group_id=None）/ bundle missing fallback / compile_error fallback / cancel-path（D2 — selector resolve 不污染下次 turn）
7. 文档收口：本文 §7 状态表 + maintenance-log + migration §12 第 6 行追加

**Out of scope（B3 不动）**

- 多群灰度 / 全量切流：B3 仅 `runtime_groups=["993065015"]`，多群是 B4，全量是 B5
- v1 PromptBuilder 任何拼接逻辑改动：`build_static` 保持原貌；v2 仅在 `build_blocks()` 拼装第一块时旁路替换；卸 v1 是 B6
- Admin SPA Runtime 切换面板：B6 范围；B3 仅暴露 counter API（可选，若有现成接入点）
- LLM 真实 prompt 行为变化外的副作用：v2 prompt blocks 走 `runtime.adapter` 注入 bot_self_id，与 v1 锚点等价；提示语效果差异由 B2 shadow compare diff log 持续监控
- private chat 切流：B3 设计上只切群（用 `group_id` 比对 `runtime_groups`）；私聊走 v1（避免管理员私聊体验突变）
- group profile dynamic block：v2 `runtime.group_profile` 是 dynamic 位置，B3 不接，归 B4

**护栏**

- D1：`grep -rn 'runtime_selector\|PersonaRuntimeSelector\|resolve_static_block\|_v2_static_text' --include='*.py'` 仅命中 `services/persona/runtime_selector.py`、`services/llm/prompt_builder.py`、`services/llm/client.py`（一处 fallback）、`kernel/router.py`（一处装配）、`tests/`；plugins/、admin/、bot.py 零命中
- D2：`PersonaRuntimeSelector.resolve_for_group()` 必须有 `pytest.raises(asyncio.CancelledError)` / `wait_for(..., timeout=0)` 回归——确保 cancel 不污染下一轮 turn 的 selector 缓存（v2 文本只在 connect 一次构建并缓存，turn-level resolve 是纯字典查找无 IO）
- D3：本文是 B3 的 D3 迁移清单
- D5：targeted 跑 `tests/test_persona_runtime_*.py` / `tests/test_persona_runtime_selector.py` / `tests/test_prompt_builder*.py` / `tests/test_llm_client*.py`；全量 pytest 前 `pkill -9 -f pytest`
- D6：B3 不触前端；不需 `npm run build` / docker rebuild
- D7：commit 前 `git stash list && git status -uno`；只 stage `services/persona/runtime_selector.py` / `services/llm/prompt_builder.py` / `services/llm/client.py` / `kernel/router.py` / `tests/test_persona_runtime_selector.py` / `tests/test_prompt_builder*.py` / 本文 / `maintenance-log.md` / `docs/migrations/persona-v2-importer.md`

---

## 1. 子任务编号与依赖

| 编号 | 任务 | 依赖 | 关键产物 |
|---|---|---|---|
| B3.1 | `PersonaRuntimeSelector` + `RuntimeSelectorCounter` | B1.4 (`load_pending_freeze`) + B2 (shadow 已验证 byte-equal) | `services/persona/runtime_selector.py`；`tests/test_persona_runtime_selector.py` 8 条 |
| B3.2 | `PromptBuilder` 接入 selector + fallback safe path | B3.1 | `services/llm/prompt_builder.py` 增 ≤ 30 行；`services/llm/client.py` 仅改 2 行 fallback |
| B3.3 | `_on_connect` 装配 selector | B3.1 + B3.2 | `kernel/router.py` 新增 ≤ 12 行；与 B2.3 hook 共栈但独立 if 块 |
| B3.4 | 单群验证 + 监控（手动） | B3.1 ~ B3.3 | bot 重启后群 993065015 真切 v2；shadow log + counter 同步增长；`storage/logs/` 无 ERROR |
| B3.5 | 文档收口（本文 §7 + maintenance-log + migration §12 第 6 行） | B3.1 ~ B3.4 | 三处文档同步 |

B3.1 单 commit；B3.2 + B3.3 同 commit（PromptBuilder 改动与 router.py 装配是同一调用面，git diff 一起读）；B3.5 收尾 commit。B3.4 是手动验证步骤，不产 commit。

---

## 2. B3.1 — `PersonaRuntimeSelector` + counter

### 2.1 设计

```python
# services/persona/runtime_selector.py
@dataclass(frozen=True)
class RuntimeSelection:
    """Result of one resolve_for_group() call."""
    use_v2: bool
    v2_static_text: str = ""        # joined static blocks; empty when use_v2=False
    fallback_reason: str = ""       # "" | "flag_off" | "group_not_listed"
                                    # | "private_chat" | "bundle_missing"
                                    # | "compile_error" | "fallback_disabled"


@dataclass
class RuntimeSelectorCounter:
    v2: int = 0
    v1_fallback: int = 0     # flag on but bundle/compile failed → fall back
    v1_default: int = 0      # flag off / group not listed / private
    last_error: str = ""
    last_reason: str = ""


class PersonaRuntimeSelector:
    def __init__(
        self,
        *,
        cfg: PersonaV2Config,
        bundle: PersonaRuntimeBundle | None,
        v2_static_text: str = "",
    ) -> None:
        self._cfg = cfg
        self._bundle = bundle
        self._v2_static_text = v2_static_text
        self._counter = RuntimeSelectorCounter()

    @property
    def counter(self) -> RuntimeSelectorCounter: ...

    def resolve_for_group(self, group_id: str | None) -> RuntimeSelection:
        """Pure, synchronous, never raises. Called per turn (hot path)."""
```

**resolve_for_group 决策树（按优先级）**

1. `cfg.runtime_consume == False` → `RuntimeSelection(use_v2=False, fallback_reason="flag_off")` → `counter.v1_default += 1`
2. `group_id is None`（私聊）→ `use_v2=False, fallback_reason="private_chat"` → `counter.v1_default += 1`
3. `group_id not in cfg.runtime_groups` → `use_v2=False, fallback_reason="group_not_listed"` → `counter.v1_default += 1`
4. `bundle is None` → `use_v2=False, fallback_reason="bundle_missing"` → `counter.v1_fallback += 1`（视 cfg.fallback_on_compile_error 决定 fallback 还是 raise；本期默认 fallback）
5. `not bundle.ok` → `use_v2=False, fallback_reason="compile_error"` → 同上；`counter.last_error = ", ".join(bundle.errors)`
6. 全部通过 → `use_v2=True, v2_static_text=self._v2_static_text` → `counter.v2 += 1`

**v2_static_text 构造（在 connect 时构建一次，selector 持有不可变 str）**

复用 B2 shadow.py 的 `_join_static_blocks` 思路：用 `_STATIC_BLOCK_ORDER = ("core.identity", "runtime.adapter", "core.guard", "core.voice", "core.knowledge", "core.examples")`，按顺序把 `bundle.compile_result.prompt_blocks` 中 `position == "static"` 的 text join 成单段（与 v1 `static_block.text` 同位置）。这一步独立函数 `join_static_blocks(bundle)` 抽到 runtime_selector.py，shadow.py 改用同一个（同 commit 把 shadow.py 那个 `_join_static_blocks` 替换为公共函数；语义等价，避免 D1 同模式漂移）。

### 2.2 风险

- **selector 是 hot path**：`resolve_for_group` 每个 turn 都被调用一次（在 LLMClient.chat() 之前），必须 O(1) 字典 / list 查找；`runtime_groups` 在 `__init__` 转 `frozenset` 缓存
- **counter 持久化**：B3 不做（in-memory）；bot 重启后清零，与 B2 一致
- **fallback_on_compile_error**：B3 默认 True（per B1.1 默认值），即 bundle 缺失或 compile 失败时 selector 返回 v1；若运维显式改 False，selector 仍返回 v1（不 raise），但记录 `fallback_reason="fallback_disabled"`，由上游 LLMClient 兜兜底——B3 不引入"raise 模式"，那是 B4 多群灰度后才考虑的护栏升级

### 2.3 验收

- 新增 `tests/test_persona_runtime_selector.py` 8 条：
  - `test_flag_off_returns_v1`：`runtime_consume=False` → `use_v2=False, reason="flag_off"`
  - `test_runtime_groups_empty_returns_v1`：flag on 但 `runtime_groups=[]` → `reason="group_not_listed"`
  - `test_group_match_returns_v2`：群命中 + bundle ok → `use_v2=True`，`v2_static_text` 非空且包含 6 段
  - `test_group_not_match_returns_v1`：群不在白名单 → `reason="group_not_listed"`
  - `test_private_chat_returns_v1`：`group_id=None` → `reason="private_chat"`
  - `test_bundle_missing_returns_v1_fallback`：bundle=None → `reason="bundle_missing"`，`counter.v1_fallback=1`
  - `test_compile_error_returns_v1_fallback`：bundle.ok=False → `reason="compile_error"`，`counter.last_error` 非空
  - `test_resolve_cancel_does_not_corrupt`（D2）：`asyncio.wait_for(asyncio.to_thread(selector.resolve_for_group, gid), timeout=0)` 抛 TimeoutError 后 counter 字段不变（resolve 是纯同步函数，cancel 不会影响——但测试锁定这个语义防回归）
- `uv run pytest tests/test_persona_runtime_selector.py -q` 全绿
- `uv run ruff check services/persona/runtime_selector.py tests/test_persona_runtime_selector.py` clean
- `uv run pyright services/persona/runtime_selector.py` 0 errors

### 2.4 回滚

revert B3.1 commit；新文件，无 caller（B3.2 才接），shadow.py 替换的 `_join_static_blocks` 同 commit 还原即可。

---

## 3. B3.2 — `PromptBuilder` 接入 + `LLMClient` fallback safe path

### 3.1 设计

`PromptBuilder` 增加：

```python
def set_runtime_selector(self, selector: PersonaRuntimeSelector | None) -> None:
    self._runtime_selector = selector

def resolve_static_block(self, group_id: str | None) -> dict[str, Any]:
    """Return v1 or v2 static block based on selector decision.

    Identical signature to ``static_block`` property's value type. Always
    returns a {"type": "text", "text": "..."} dict.
    """
    if self._runtime_selector is None:
        return self._static_block
    selection = self._runtime_selector.resolve_for_group(group_id)
    if selection.use_v2 and selection.v2_static_text:
        return {"type": "text", "text": selection.v2_static_text}
    return self._static_block
```

`build_blocks()` 第 138 行 `blocks: list[dict[str, Any]] = [self._static_block]` 改为：

```python
blocks: list[dict[str, Any]] = [self.resolve_static_block(group_id)]
```

`LLMClient.chat()` line 2103 / 2105 两处 `system_blocks = [self._prompt.static_block]` 改为 `system_blocks = [self._prompt.resolve_static_block(group_id)]`——保证 build_blocks 抛异常的 fallback 路径也尊重 selector（避免"build_blocks 偶发失败 → 切回硬编码 v1 → 与 v2 不一致"漂移）。

### 3.2 风险

- **`build_static` 保留**：B3 不动 `build_static`——`_static_block` 仍由 `_on_connect` 调用 `build_static` 填好，作为 v2 失败时的默认值。这是单群灰度安全网：v2 路径任何环节出问题，都退回 v1 文本（已验证可用）
- **`resolve_static_block` 必须无 IO**：selector 在 connect 时已构造好 v2 文本，turn 内只查字典；不能在 hot path 里调 `load_pending_freeze`（那是 connect 一次性的）
- **selector 默认 None**：未装配时 `resolve_static_block` 返回 v1，等价于 B3 之前的行为——这是 B3 对未配置 `[persona_v2]` 的老用户的兼容承诺
- **build_blocks(group_id=None) 私聊**：selector resolve_for_group(None) 返 v1，等价于私聊不切流——符合 §0 设计

### 3.3 验收

- D1 同模式扫描：`grep -rn 'resolve_static_block\|_runtime_selector\|set_runtime_selector' --include='*.py'`：
  - `services/llm/prompt_builder.py` 主体（≤ 30 行）
  - `services/llm/client.py` 2 处 fallback 调用
  - `kernel/router.py` 1 处 set_runtime_selector
  - `tests/` fixture / 断言
- 无回归：`pytest tests/test_persona_runtime_loader.py tests/test_persona_compiler.py tests/test_persona_shadow.py tests/test_prompt_builder*.py tests/test_llm_client*.py -q` 全绿
- 手动验证 §3.4

### 3.4 回滚

revert B3.2 commit；`build_blocks` 改回硬编码 `[self._static_block]`，`LLMClient` 两处改回 `[self._prompt.static_block]`，删除 `set_runtime_selector` / `resolve_static_block` 即可；selector 模块本身（B3.1）保留为死代码，不影响运行。

---

## 4. B3.3 — `_on_connect` 装配 selector

### 4.1 设计

`kernel/router.py::_on_connect` 在 B2.3 shadow hook 之后追加（同样 flag-gated；与 shadow 是兄弟分支，互不依赖）：

```python
# B3 runtime cutover (flag-gated; defaults off)
runtime_selector = None
if persona_v2_cfg is not None and persona_v2_cfg.runtime_consume:
    from services.persona.runtime import load_pending_freeze
    from services.persona.runtime_selector import PersonaRuntimeSelector, join_static_blocks

    bundle = load_pending_freeze(persona_v2_cfg.persona_id)
    v2_text = ""
    if bundle is not None and bundle.ok:
        v2_text = join_static_blocks(bundle)
    runtime_selector = PersonaRuntimeSelector(
        cfg=persona_v2_cfg,
        bundle=bundle,
        v2_static_text=v2_text,
    )
    if bundle is None or not bundle.ok:
        if persona_v2_cfg.fallback_on_compile_error:
            _base_logger.bind(channel="persona_runtime").warning(
                "v2 bundle unavailable; falling back to v1 | bundle_ok={} errors={}",
                bundle is not None and bundle.ok,
                tuple(bundle.errors) if bundle else (),
            )
        else:
            # fallback disabled; selector still returns v1 but logs error
            _base_logger.bind(channel="persona_runtime").error(
                "v2 bundle unavailable AND fallback disabled | bundle_ok={}",
                bundle is not None and bundle.ok,
            )

ctx.prompt_builder.set_runtime_selector(runtime_selector)
ctx.runtime_selector = runtime_selector  # admin/API readonly access
```

`kernel/types.py::PluginContext` 新增：

```python
# Persona v2 — runtime selector (B3; flag-gated; per-turn read)
runtime_selector: Any = None
```

### 4.2 风险

- **import 时机**：lazy import 进 if 块，flag=off 时无开销（与 B2.3 同模式）
- **bundle 一次加载**：每次 connect 重新 load—— allow 运维改 `_pending_freeze/` 后重启 bot 立即生效（与 shadow 同语义，B5 全量切流后再考虑 LRU）
- **kernel/ 已不再 forbidden**：B2.3 已开第一刀；B3 在同函数加 ~30 行 if 块；router.py 改动累计 ~50 行，仍可一眼看清 git diff

### 4.3 验收

- D1 扫描：`grep -rn 'set_runtime_selector\|PersonaRuntimeSelector\|join_static_blocks' --include='*.py'`：
  - `services/persona/runtime_selector.py` 主体
  - `services/persona/shadow.py` 改用 `join_static_blocks`（公共化）
  - `services/llm/prompt_builder.py` set + resolve
  - `services/llm/client.py` resolve（fallback 路径）
  - `kernel/router.py` 1 处 `_on_connect` 装配
  - `kernel/types.py` `runtime_selector` 字段
  - `tests/`
  - 其他路径（`bot.py`、`admin/`、`plugins/`）零命中
- 跑 `pytest tests/test_persona_runtime_*.py tests/test_persona_compiler.py tests/test_persona_shadow.py tests/test_prompt_builder*.py tests/test_llm_client*.py -q` 全绿
- 全量 pytest（`pkill -9 -f pytest` 后）：≥ 1558 passed（B2 后基线），新增 8 条 selector 测试 → 1566 passed / 8 skipped

### 4.4 回滚

revert B3.3 commit；router.py 删 ~30 行 if 块；types.py 删 1 行字段。Selector + PromptBuilder 改动（B3.2）仍是死代码（PromptBuilder._runtime_selector 始终 None → resolve_static_block 返 v1）。

---

## 5. B3.4 — 单群验证 + 监控（手动）

### 5.1 上线步骤

1. 把 `config.toml` 改：
   ```toml
   [persona_v2]
   persona_id = "fengxiaomeng-v2"
   runtime_consume = true
   shadow_compare = true     # 双轨持续监控；与 v2 切流并行运行
   runtime_groups = ["993065015"]
   fallback_on_compile_error = true
   ```
2. `docker compose restart bot`（D6：未改前端、未改代码，restart 即可；改代码要 `--build`）
3. 等 bot 在群 993065015 收到第一条消息并触发回复
4. 观察以下三个证据点

### 5.2 证据点

- **v2 命中**：`storage/logs/bot.log` 找 `persona_runtime` channel `WARNING` 日志为零；admin counter（B6 暴露后可视化）显示 `runtime_selector_total{result="v2"} >= 1`
- **shadow diff log 同步**：每 connect 或 reconnect 触发一次 shadow compare 写一行；JSONL `has_divergence` 字段值持续记录（v1↔v2 字面量当然有差异——锚点是这些差异**不**新增 axes，与 §9 已声明的 3 条 divergent axes 一致）
- **prompt 行为质量**：人工跟群对话 30 分钟，对比"v1 ↔ v2 切换前后"是否出现 persona 漂移（自称漂移、价值观异常、行为指令丢失）。由用户做最终验收

### 5.3 回退（紧急）

只需把 `config.toml` 改：

```toml
[persona_v2]
runtime_consume = false   # 一行回滚 v1
```

`docker compose restart bot`，30 秒内回到 v1。`_pending_freeze/` 与 B1/B2/B3 代码保留，不影响下次 retry。

---

## 6. 提交节奏

按 B3.1 → B3.2+B3.3 → B3.5 顺序产出 3 个独立 commit。**每个 commit 落地后等用户显式说"commit"才执行 `git commit`**——B3 是首次真切流，比 B2 更敏感。

| commit | 目标 | 文件 |
|---|---|---|
| B3.1 | `PersonaRuntimeSelector` + counter + 8 tests + shadow.py 公共化 join_static_blocks | `services/persona/runtime_selector.py` + `services/persona/__init__.py` + `services/persona/shadow.py`（替换 `_join_static_blocks`） + `tests/test_persona_runtime_selector.py` |
| B3.2+B3.3 | PromptBuilder + LLMClient + router.py + types.py | `services/llm/prompt_builder.py` + `services/llm/client.py` + `kernel/router.py` + `kernel/types.py`；新增 `tests/test_prompt_builder_runtime.py` ≥ 4 条 |
| B3.5 | 文档收口 | 本文 §7 + maintenance-log + migration §12 第 6 行 ⏳→✅ |

### Commit message 模板

```
feat(persona/runtime): B3.{n} — {short title}

- {bullet 1: behavior}
- {bullet 2: tests}
- {bullet 3: docs / migrations / maintenance-log 链接}

flag-gated; runtime_consume defaults off; runtime_groups defaults [];
single-group gray (993065015); fallback_on_compile_error=true preserves v1.
```

---

## 7. 当前状态

| 编号 | 状态 | 落地证据 |
|---|---|---|
| B3.1 | ✅ 已落地 | commit `eac2d1e` — `services/persona/runtime_selector.py::PersonaRuntimeSelector` + `RuntimeSelection` + `RuntimeSelectorCounter`；公共化 `join_static_blocks`（shadow.py 替换 `_join_static_blocks`）；`tests/test_persona_runtime_selector.py` 8 条 |
| B3.2 | ✅ 已落地 | commit `e5881f0` — `services/llm/prompt_builder.py::PromptBuilder.set_runtime_selector` + `resolve_static_block`；`build_blocks()` 第一块改走 resolve；`services/llm/client.py` 两处 fallback 同样走 resolve；`tests/test_prompt_builder_runtime.py` 7 条 |
| B3.3 | ✅ 已落地 | 同 B3.2 commit — `kernel/router.py::_on_connect` B3 装配 30 行（lazy import + load_pending_freeze + selector 装配）；`kernel/types.py::PluginContext.runtime_selector` 字段；shadow hook 与 runtime hook 兄弟分支独立 if 块 |
| B3.4 | ⏳ 待手动验证 | 由用户做最终验收（"我最终做上线前最后验收"）：toml 改 `runtime_consume=true` + `runtime_groups=["993065015"]` + `restart bot` → 群 993065015 收到至少 5 轮回复且 `storage/logs/bot.log` 无 `persona_runtime` ERROR；B3.4 跟踪期间 2026-05-27 修复 parity audit substring anchor 假阳性（[parity_audit.py](../../services/persona/parity_audit.py) 的 `_first_line` → `_meaningful_anchors`）+ 补 source.md `bot_self_id_hint` / `admins` front matter，importer/freeze 重跑后 6 axes 端到端 aligned；rebuild bot 镜像后由下次 connect-time shadow log 复核 |
| B3.5 | ✅ 已落地 | 当前 commit — 本文 §7 回填 + maintenance-log 当日条目 + migration §12 第 6 行 ⏳→✅ |

---

## 8. 出口标准（B3 完成的判定）

全部满足才能转入 B4：

- [ ] `BotConfig().persona_v2.runtime_consume` 默认 False / `runtime_groups` 默认 `[]`（B1.1 6 条 fixture 已锁定）
- [ ] `PersonaRuntimeSelector.resolve_for_group()` 永不 raise（test 锁定）
- [ ] `runtime_consume=False` 或 `runtime_groups=[]` 时 PromptBuilder 行为与 B2 等价（即第一块仍是 `_static_block`；test 锁定）
- [ ] `runtime_consume=True AND group_id in runtime_groups AND bundle.ok` 时 PromptBuilder 第一块为 v2 join 文本（test 锁定）
- [ ] bundle 缺失 / compile 失败 + `fallback_on_compile_error=True` 时静默 fallback v1（test 锁定）
- [ ] `grep -rn 'PersonaRuntimeSelector\|set_runtime_selector\|resolve_static_block' --include='*.py'` 仅命中 §4.3 列出的 6 处文件
- [ ] `uv run ruff check services/persona/ services/llm/prompt_builder.py services/llm/client.py kernel/router.py kernel/types.py tests/test_persona_*` clean
- [ ] `uv run pytest tests/test_persona_*` 全绿；全量 pytest ≥ 1566 passed
- [ ] B3.4 手动验证：群 993065015 在 v2 模式下收到至少 5 轮回复，shadow diff log 与 counter 同步增长，`storage/logs/bot.log` 无 `persona_runtime` ERROR
- [ ] migration doc §12 第 6 行 "PromptBuilder / LLMClient 注入 v2 prompt blocks" 从 ⏳ 改为 ✅
- [ ] maintenance-log B3 当日条目五段齐
