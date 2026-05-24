# Persona Source Importer 上线准备清单

> 状态：2026-05-24 立项；本文是 **dry-run → runtime 切流**（Part B S6'~S9'/S12' 主体）的执行总纲，**不在本轮直接动 runtime 代码**。
>
> 上游：
> - [persona-source-importer.md](./persona-source-importer.md)
> - [persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)
> - [persona-source-importer-acard-execution.md](./persona-source-importer-acard-execution.md)
>
> 旁支证据：
> - [persona-s12-parity-audit-execution.md](./persona-s12-parity-audit-execution.md)
> - [persona-group-override-full-execution.md](./persona-group-override-full-execution.md)
> - [persona-legacy-instruction-md-execution.md](./persona-legacy-instruction-md-execution.md)
> - [persona-part-b-main-execution.md](./persona-part-b-main-execution.md)

---

## 0. 范围与护栏

**In scope（本文负责清点 + 起草）**
- dry-run → runtime 切流前的 **gate / feature flag / 灰度策略 / 回滚剧本**
- runtime 消费 v2 draft 的 **入口点 / 切流序列 / 验收信号**
- 切流后回归 dry-run 的物理路径（feature flag off + 配置 unchanged）

**Out of scope（本文不动）**
- 任何 runtime 代码改动（保留给 B1-B5 单独发令开干）
- v1 `PromptBuilder` / `LLMClient` / `GroupChatScheduler` 的删除（v1 至少留到全量切流后两个版本作 fallback）
- `kernel.config.GroupOverride` 字段重定义（仍是只读快照）
- admin Soul SPA 编辑入口（v1 / v2 共存期不动）

**护栏（沿用 D1-D7）**
- D1 同模式扫描：每个切流步骤改前先 grep 同 site，写入 commit body
- D2 cancel-path 测试：runtime 切流必加 cancel 回归
- D3 重构带迁移清单：每个 B-step 必须填本文 §4 切流序列表
- D5 全量 pytest 前 `pkill -9 -f pytest`
- D6 admin SPA bind mount，前端只 build；改 `.py` 才 rebuild bot
- D7 deploy 前必跑 `git stash list && git status -uno`；`storage/*.db*` 走 `.gitignore` 物理护栏

---

## 1. dry-run 闭环现状（已落地，作切流入口）

| 项 | 落地证据 | 文件 |
|---|---|---|
| Importer Part A 首版 | source → 15 partial draft + report；`/api/admin/persona/import` | `services/persona/writer.py`、`services/persona/builder.py`、`admin/routes/api/persona_importer.py` |
| Part A tail #1/#7/#5 | `persona.yaml.identity.personality` / `memory.yaml.paragraph+entity_index` / `adapter.yaml.permissions.admins[]` | 同上 |
| Part B dry-run | SystemModule catalog (27+7) + RuntimeStateBus + 9 默认模板 + compiler `--compile-dry-run` | `services/system_module/`、`services/persona/compiler.py` |
| Part B prompt source 映射 | #3 instruction → guard.behavior_instructions / #4 self-id → adapter.bot_identity / #8 reply_style+custom_prompt → runtime.per_group_overrides | `services/persona/builder.py`、`services/persona/compiler.py` |
| A1 parity 15 字段 | `services/persona/parity_audit.py::GroupOverrideSnapshot` 15 字段 + `v2_extended` status + `group_profile.fields` axis | `services/persona/parity_audit.py`、`tests/test_persona_parity_audit.py` |
| A2 admins / proactive prompt block | source `## 8.4 行为指令 / 插话方式` + adapter.permissions.admins / persona.identity.proactive_rules → compiler `core.identity` / `core.guard` | `services/persona/builder.py`、`services/persona/compiler.py` |
| A3 parity API + SPA | `GET /api/admin/persona/parity/{id}` + `PersonaImporterView.vue` Parity 折叠面板 | `admin/routes/api/persona_importer.py:151`、`admin/frontend/src/views/persona/PersonaImporterView.vue` |
| A4 Issues / Fields 行号跳转 | chip → focusSourceLines（textarea focus + setSelectionRange + scrollTop，buffer 3 行） | `admin/frontend/src/views/persona/PersonaImporterView.vue` |
| A5 文档收口 | 主执行文档 §2 H/I/J/K/L + §9 长尾扩展段；维护日志当日条目 | `docs/tracking/persona-source-importer-remediation-execution.md`、`maintenance-log.md` |

> dry-run / runtime 隔离已物理验证：`services.persona.compiler.compile_persona_dry_run` 只被 `admin/routes/api/persona_importer.py` 与 `services/persona/importer.py` 引用；v1 `PromptBuilder` / `LLMClient` / `GroupChatScheduler` / 任何 plugins 完全不引用 `services.persona`。

---

## 2. 切流前必做项（gate / flag / 灰度）

### 2.1 Parity 全 axis aligned 闸门

切流 **每一步开始前**必须满足：

| axis | 当前 dry-run 状态 | 切流前期望 |
|---|---|---|
| `identity_personality` | aligned（happy path） | aligned（不变） |
| `bot_self_id` | aligned（提供 hint 时） | aligned；source 必须写 `bot_self_id_hint` |
| `behavior_instruction` | aligned（source §8.4 写明时） | aligned；source §8.4 必填 |
| `admins` | aligned（A2 已写 prompt block） | aligned（不变） |
| `proactive_rules` | aligned（A2 已写 prompt block） | aligned（不变） |
| `group_profile`（reply_style / custom_prompt） | aligned | aligned（不变） |
| `group_profile.fields`（13 字段） | `v2_extended`（v1 不输出，v2 已 dry-run 输出） | `v2_extended` 或 `aligned` 任一即可 |

**闸门实现**：admin SPA Parity 面板顶部标 `has_divergence` 红 / `all aligned` 绿；`has_divergence=true` 时切流按钮置灰，tooltip 提示 "修复 source.md 后 import + freeze 再切流"。

### 2.2 Feature flag 设计

| flag | 类型 | 默认 | 作用域 | 含义 |
|---|---|---|---|---|
| `persona.v2.runtime_consume` | bool | `false` | 全局 / per-group | runtime 是否消费 `_pending_freeze/` 的 v2 draft；off 时完全走 v1 |
| `persona.v2.runtime_groups` | list[str] | `[]` | 全局 | 灰度群白名单；空时即使 `runtime_consume=true` 也只对 DM 生效 |
| `persona.v2.shadow_compare` | bool | `false` | 全局 | 切流期间 runtime 同时算 v1 + v2 prompt block，diff 写日志（不发 LLM） |
| `persona.v2.fallback_on_compile_error` | bool | `true` | 全局 | v2 compile 失败时是否回退 v1（保险开关，永远 true 直到全量切完两个版本） |

**落地位置**：`config.toml` 新增 `[persona.v2]` 段；`BotConfig` 加对应字段（Pydantic）；admin SPA「系统配置 → Persona」面板暴露开关 + 灰度群编辑（B6'）。

### 2.3 灰度策略

```
B1 dry-run 全 axis aligned
   └─ B2 启用 shadow_compare → runtime 双算 + diff 日志（无侧效应，可灰度 1 天）
       └─ B3 单 group 灰度（runtime_groups=[一个非生产关键群]）+ runtime_consume=true
           └─ B4 多 group 灰度（5~10 个群，含 1 个高活跃群）
               └─ B5 全量切流（runtime_groups=[]，runtime_consume=true 对所有 group/DM）
                   └─ B6 v1 路径降级为 fallback-only；2 个版本观察期后删 v1 拼接代码
```

**每一步停留时间**：B2 ≥ 1 天观察 diff 日志；B3 ≥ 3 天；B4 ≥ 7 天；B5 ≥ 14 天再 B6。

### 2.4 回滚剧本

| step | 回滚动作 | 命令 / 路径 | RTO |
|---|---|---|---|
| B2 → B1 | 关 shadow_compare | `config.toml` 改 `persona.v2.shadow_compare = false` + `docker compose restart bot` | < 30s |
| B3 → B2 | 单群退灰度 | `runtime_groups` 移除目标群 + restart bot | < 30s |
| B3/B4 → B2 | 全部退灰度 | `runtime_consume = false` + restart bot；v2 draft 仍留盘 | < 30s |
| B5 → B4 | 退回灰度 | `runtime_groups = [...]` 重设白名单 + restart bot | < 30s |
| B6 → B5 | 不允许直接退（v1 已删） | 必须 git revert B6 commit + 镜像重建 | 5-10 min |

**回滚物理护栏**：每个 B-step commit 必须独立、有明确 revert 路径；切流配置走 `config.toml` 不走代码硬编码（确保 RTO < 1 min）。

---

## 3. 切流前剩余阻塞项

| 项 | 当前状态 | 阻塞 step | 解决路径 |
|---|---|---|---|
| `_pending_freeze/` → runtime 消费协议 | 未定义 | B1 准备 | runtime 启动时若 `runtime_consume=true` 且 `_pending_freeze/<id>/` 存在，加载并校验 schema；否则走 v1 |
| `compile_persona_runtime()` API | 未实现（只有 `compile_persona_dry_run`） | B1 准备 | 新增 runtime 版（同样的 compiler，差别在 mode 标记 + 错误时 raise vs 报告） |
| Shadow compare 落地点 | 未实现 | B2 实现 | `LLMClient.chat()` 在 `runtime_consume=false` + `shadow_compare=true` 时双算 prompt block，diff 写 `storage/persona_shadow_diff.log` |
| Runtime 切流入口（v2 PromptBlock 注入） | 未实现 | B3 实现 | `PromptBuilder.build_static()` / `_build_group_profile_block()` 接收 `persona_v2_blocks: list[PromptBlock] \| None`；非 None 时用 v2，否则用 v1 |
| Admin SPA Persona Runtime 切换面板 | 未实现 | B6 实现 | 新增 `PersonaRuntimeView.vue`，展示当前 flag 状态 + 灰度群列表 + 紧急回滚按钮 |
| 监控指标 | 未定义 | B2 起 | `persona_v2_compile_total{status=ok\|error}` / `persona_v2_shadow_diff_total{axis}` / `persona_v2_runtime_consume_total{group_id}` 三个 counter |

---

## 4. B-step 切流序列（待发令）

**本节不开干**，仅作起草。每一步落地时复制到独立执行追踪文档（`persona-runtime-cutover-B{n}-execution.md`）走 D3 迁移清单流程。

| step | 名称 | 落地目标 | 验收 |
|---|---|---|---|
| B1 | runtime 消费协议 + `compile_persona_runtime()` API | `services/persona/compiler.py` 新增 runtime 入口；`config.toml` 注入 `[persona.v2]` 段；`BotConfig.persona_v2` 字段 | unit + 启动配置回归；flag off 时全链路无变化 |
| B2 | Shadow compare | `LLMClient.chat()` 双算 + diff 日志；admin SPA Shadow Diff 面板 | 24 小时无 axis-level divergence；diff 日志体积 < 1 MB/天 |
| B3 | 单群灰度切流 | `PromptBuilder.build_static()` / `_build_group_profile_block()` 接收 v2 blocks | 灰度群无功能回归（人工 + tool call 计数 + cache hit rate 不降） |
| B4 | 多群灰度 | 同上 + 灰度群扩展 | 同上；token usage 总量 ±5% 内 |
| B5 | 全量切流 | `runtime_consume=true` 对所有群 / DM | 同上；7 天稳定后进入 B6 |
| B6 | v1 拼接代码下线 | 删 `PromptBuilder` admins/proactive/instruction 拼接；保留 `runtime_consume=false` 时的 fallback 死代码 2 个版本 | targeted pytest + 24 小时 prod 监控 |

---

## 5. 监控与日志

| 信号 | 入口 | 阈值 | 报警 |
|---|---|---|---|
| `persona_v2_compile_total{status=error}` | `services/persona/compiler.py::compile_persona_runtime` | > 0 | warn → 自动回退 v1 |
| `persona_v2_shadow_diff_total{axis}` | `LLMClient.chat()` shadow path | > 5/min | warn 排查 |
| `persona_v2_runtime_consume_total{group_id}` | `PromptBuilder` v2 hook | 0（开启灰度后） | error |
| Cache hit rate | `services/llm/usage.py` | 切流后 -10% 内可接受 | warn |
| Tool call rate | 同上 | 切流后 ±20% 内 | warn |
| Avg latency | 同上 | 切流后 +20% 以内 | warn |
| Admin Parity panel `has_divergence` | `/api/admin/persona/parity/{id}` | true | error 阻断切流按钮 |

**日志路径**
- Shadow diff: `storage/persona_shadow_diff.log`（rotate 7 天）
- Compile error: 沿用 loguru `channel=persona`
- Runtime consume audit: `storage/persona_runtime_consume.audit.log`（每个群每次切流写一行）

---

## 6. 风险登记

| 风险 | 等级 | 触发场景 | 缓解 |
|---|---|---|---|
| v2 compiler 渲染漂移导致 prompt 变化引起人格回归 | 高 | B3 灰度后人格表现明显异常 | shadow compare（B2）必须 ≥ 24h 零 divergence；切流后保留 v1 fallback 至少 2 个版本 |
| `_pending_freeze/` 文件权限 / 编码问题 | 中 | runtime 启动时无法读 / yaml 解析失败 | `fallback_on_compile_error=true`；启动失败 alarm；`compile_persona_runtime` 必加 fuzz test |
| 灰度期间 cache 频繁失效 | 中 | shadow compare 双算导致 cache prefix 变化 | shadow path **不发 LLM** 仅算本地 prompt；不影响真实 cache |
| Admin SPA 切流面板误操作 | 中 | 误把生产群加进 `runtime_groups` | NPopconfirm 二次确认 + 操作审计日志 |
| Cancel-path 漏测 | 高 | runtime 切流期 bot shutdown 触发 | D2：`compile_persona_runtime` 必加 `pytest.raises(asyncio.CancelledError)` 回归 |
| v1 / v2 长期共存导致代码腐化 | 低 | B5 → B6 拖延 | B5 后强制 14 天观察期，到期自动开 B6 PR |

---

## 7. 文档与流程对齐

切流期间需同步维护的文档：

| 文档 | 触发动作 | 频率 |
|---|---|---|
| `docs/migrations/persona-v2-importer.md` | 每个 B-step 完成后追加旧→新对照行 | 每 B-step |
| `docs/tracking/persona-runtime-cutover-B{n}-execution.md` | 每个 B-step 独立执行追踪 | 每 B-step |
| `maintenance-log.md` | 每个 B-step deploy 后追加条目（变更类型 / 内容 / 影响 / 验证 / 回滚） | 每次 deploy |
| `CHANGELOG.md` | B5 全量切流时一次性写 v1.6.0 入口；B6 删 v1 时写 v2.0.0 | v1.6.0 / v2.0.0 |
| `CLAUDE.md` | 切流后 architecture 段更新 PromptBuilder 描述 | B6 完成后 |

---

## 8. 立项 checklist（开始 B1 前必须满足）

- [x] dry-run 全 axis aligned（A1-A5 落地，2026-05-24 已确认）
- [x] Parity API + SPA 视图（A3 落地）
- [x] Issues / Fields 行号跳转（A4 落地）
- [x] 主执行文档 + maintenance-log 当日条目（A5 落地）
- [x] dry-run / runtime 物理隔离审计（本文 §1 已确认 services.persona 不被 runtime 引用）
- [ ] B1 执行追踪文档立项
- [ ] `[persona.v2]` 配置段 + `BotConfig.persona_v2` 字段设计稿
- [ ] `_pending_freeze/` runtime 消费协议设计稿
- [ ] 监控埋点设计稿（counter 名称 + 标签 + 报警阈值）
- [ ] Admin SPA Runtime 切换面板设计稿（线框 + 操作流程）

---

## 9. 当前状态

**2026-05-24**：本文立项；A 档 dry-run 扩展闭环；切流序列待发令。

| 编号 | 状态 | 备注 |
|---|---|---|
| 上线准备 §1 现状清单 | ✅ 完成 | dry-run 闭环点位齐 |
| 上线准备 §2 gate / flag / 灰度设计 | ✅ 完成（设计稿） | 实现待 B1 |
| 上线准备 §3 阻塞项盘点 | ✅ 完成 | 6 项待 B1-B6 解决 |
| 上线准备 §4 B-step 切流序列起草 | ✅ 完成（设计稿） | 待用户发令开 B1 |
| 上线准备 §5 监控信号 | ✅ 完成（设计稿） | 待 B2 实现埋点 |
| 上线准备 §6 风险登记 | ✅ 完成 | 6 项已记录 |
| 上线准备 §8 立项 checklist | 🟡 部分满足 | 5/10 已勾 |
