# Persona Source Importer 整改执行追踪

> 状态：Part A S1-S5 后端/CLI 首版完成；S6/S10' admin SPA 首版闭环完成；Part B dry-run 闭环完成
> 启动时间：2026-05-24
> 执行人：Codex
> 上游步骤：[persona-source-importer-remediation.md](./persona-source-importer-remediation.md)
> 原方案：[persona-source-importer.md](./persona-source-importer.md)
> Part A 发令：2026-05-24 用户确认“继续，直到完成”

---

## 0. 执行规则

1. 每个步骤开始前先写清楚：细分动作、风险、回滚方式、验收证据。
2. 每个步骤完成后立刻回填：实际改动、验证命令或人工核对证据、遗留风险。
3. P0/P1/P2 只做文档和配置整改，不启动 `services/` / `plugins/` / `kernel/` 运行时实现。
4. 如果某一步发现上游 remediation 与实际仓库冲突，先记录冲突，再按现有仓库事实修正执行路径。

---

## 1. 当前仓库事实

| 项 | 事实 | 影响 |
|---|---|---|
| 主方案 | `docs/tracking/persona-source-importer.md` 已含 GPT 与 deepseek 两份审计 | 执行文档以双审计交集为准 |
| 整改总表 | `docs/tracking/persona-source-importer-remediation.md` 已存在 | 本文档不重复设计，只追踪执行 |
| v2 spec | `docs/persona-spec-format.md` 仍是 12 文件结构 | P0-1 必须先补 v2.1 扩展 |
| config 路径 | `.gitignore` 当前忽略整个 `/config/` | P0-2 新增 `_defaults/v2` 时必须安全放行该子树 |
| 运行时实现 | 本轮不启动 S1' 代码实现 | 完成 P0~P2 后停在“可进入实现”状态 |
| 工作区核查 | AGENTS 建议 `$HOME/OmubotWorkspace/omubot`，但当前该路径不是 git 仓库；本轮实际 git 根目录为 `/Volumes/OmubotDisk/omubot` | 不触碰旧 exFAT 路径；最终验证以当前 git 根目录为准 |

---

## 2. 步骤总览

| 步骤 | 名称 | 状态 | 负责人 | 完成证据 |
|---|---|---|---|---|
| P0-1 | 升级 `persona-spec-format.md` 到 v2.1 扩展 | ✅ 完成 | Codex | `grep -c 'state.yaml\|thinker.yaml\|system.yaml' docs/persona-spec-format.md` = 17 |
| P0-2 | 建立 `config/persona/_defaults/v2/` 默认模板 | ✅ 完成 | Codex | 4 个模板文件落地；`config/config.json` 仍被忽略 |
| P0-3 | 拆分 §16 到 `system-module-architecture.md` | ✅ 完成 | Codex | importer 1137 行；架构文档 658 行；importer 仅保留 §16 引用块 |
| P0-4 | 统一 Freeze 为 `Pending Freeze` | ✅ 完成 | Codex | 正文新增 `Pending Freeze vs Schema Freeze`，Q6 改为 compiler 前仅暂存 |
| P0-5 | 明确首版 15 文件 skeleton 输出 | ✅ 完成 | Codex | §5.1 输出矩阵列出 15 YAML + `modules/_README.md` |
| P1-1 | API 前缀对齐 `/api/admin/persona/*` | ✅ 完成 | Codex | S5 已改为 `/api/admin/persona/*`，审计历史保留旧问题 |
| P1-2 | 改为 `persona_import` LLMTask/profile | ✅ 完成 | Codex | 合同区改为 `LLMRequest(task=\"persona_import\")`；Q3 更新 |
| P1-3 | draft 元数据外挂到 `_import_report.json` | ✅ 完成 | Codex | §5.3 改为纯 YAML + `_import_report.json` 字段元数据 |
| P1-4 | hard_rule 三类 enforce 拆分 | ✅ 完成 | Codex | §7.2 / §9 改为 pattern/judge/eval 三类 |
| P1-5 | §17 决策表补 `输入来源` 列 | ✅ 完成 | Codex | §17.1 17 行决策均补来源/归属 |
| P1-6 | importer 文档同步收口 | ✅ 完成 | Codex | 文首/§1/§17 改为 v2.1 spec + Part A/Part B 边界 |
| P2-1 | 默认模板出处更正 | ✅ 完成 | Codex | 合同区改为 `_defaults/v2` + `kernel/config.py`/`config.json` 只读投影 |
| P2-2 | 删除已延后的 UI 首版承诺 | ✅ 完成 | Codex | §7.3/§7.4 标为 S10' 后续；`Draft (12)` 改为 `Draft (15 skeleton)` |
| P2-3 | 补 `.gitignore` 物理护栏 | ✅ 完成 | Codex | P0-2 已提前完成；`git check-ignore` 已验证 |
| P2-4 | 新增最小 `source.md` 模板 | ✅ 完成 | Codex | §4.2 新增最小模板，原模板改为 §4.3 进阶模板 |
| P2-5 | 新增“运维配置”第三类 | ✅ 完成 | Codex | §3 改为创作/默认模板/工程 skeleton/运维配置 |
| P2-6 | 字段映射表补“缺失时行为”列 | ✅ 完成 | Codex | §6 表格 30 行均补 `缺失时行为` |
| P2-7 | v2 schema 标注 proposal-level | ✅ 完成 | Codex | 文首新增 proposal-level + 松 schema 声明 |
| P2-8 | 膨胀控制条款 | ✅ 完成 | Codex | importer 主文档 1330 行；§20 维护纪律落地 |
| B0 | S6/S10' 范围确认与追踪启动 | ✅ 完成 | Codex | 本文新增 `## 6. S6/S10' admin SPA 实施追踪` |
| B1 | `source.md` 读写 API + 测试 | ✅ 完成 | Codex | `tests/test_persona_importer_api.py` 3 passed |
| B2 | Admin Persona Importer 页面、路由与菜单 | ✅ 完成 | Codex | `vue-tsc --noEmit` 通过 |
| B3 | S6/S10' 验证与文档收口 | ✅ 完成 | Codex | `45 passed` + `vue-tsc` + `npm run build` |
| C0 | Part B S1' 启动与范围确认 | ✅ 完成 | Codex | Part B 范围锁定为 S1' dry-run 骨架 |
| C1 | SystemModule 契约模型与默认目录 | ✅ 完成 | Codex | 新增 `services/system_module/{catalog,models,validator}.py` |
| C2 | RuntimeStateBus 骨架与 dry-run 校验 | ✅ 完成 | Codex | `tests/test_system_module.py` 8 passed |
| C3 | S1' 验证、迁移清单与维护日志 | ✅ 完成 | Codex | `18 passed` + 迁移清单/维护日志已同步 |
| C4 | S2'/S3' source 扩展与 9 默认模板 | ✅ 完成 | Codex | 9 默认模板 + §12/§11.2 抽取测试通过 |
| C5 | S5' SystemModule validator 接入 importer report | ✅ 完成 | Codex | `_system_module_validation` 写入 report |
| C6 | S11' persona compiler dry-run | ✅ 完成 | Codex | `services/persona/compiler.py` + CLI `--compile-dry-run` |
| C7 | Part B dry-run 闭环验证与收口 | ✅ 完成 | Codex | `23 passed` + ruff |

---

## 3. 执行日志

### P0-1 升级 `persona-spec-format.md`

**开始前拆分**

1. 在 `docs/persona-spec-format.md` 末尾追加 `## v2.1 扩展：runtime state / thinker / system`。
2. 定义 `state.yaml`、`thinker.yaml`、`system.yaml` 的最小 schema。
3. 定义 `modules/<id>/module.yaml` 的最小契约。
4. 说明 v2.0 12 文件仍为 minimal core，v2.1 是 importer 目标扩展。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| v2.1 字段过细，反过来锁死 importer | 中 | 只写最小 schema 与字段族，不写实现细节 |
| 与原 v2.0 章节冲突 | 中 | 明确 v2.0 minimal core 与 v2.1 extension 的关系 |
| 后续 §16 拆分后引用变动 | 低 | 使用文件名级别引用，避免强绑行号 |

**完成后回填**

- 实际改动：在 `docs/persona-spec-format.md` 末尾追加 `## v2.1 扩展：runtime state / thinker / system`。
- 覆盖内容：v2.1 目录结构、`state.yaml`、`thinker.yaml`、`system.yaml`、`modules/<id>/module.yaml` 最小 schema、v2.1 文件优先级补充。
- 验证证据：
  - `grep -c 'state.yaml\|thinker.yaml\|system.yaml' docs/persona-spec-format.md` 输出 `17`。
  - `grep -n 'v2.1 扩展\|state.yaml\|thinker.yaml\|system.yaml\|modules/<id>/module.yaml' docs/persona-spec-format.md` 能定位新增章节与四个 schema 锚点。
- 遗留风险：v2.1 仍是 proposal-level extension，后续 P0-5 / P1-3 需要保证 importer draft 与该扩展保持一致。

### P0-2 建立 `config/persona/_defaults/v2/`

**开始前拆分**

1. 创建 `config/persona/_defaults/v2/`。
2. 写入 `guard.yaml`、`eval.yaml`、`trace.yaml` 三份首版默认模板。
3. 写入 `README.md`，说明首版只覆盖 3 个工程模板，其余工程文件由 importer 生成空 skeleton。
4. 修正 `.gitignore`：继续忽略真实 `/config/` 私有配置，但放行 `config/persona/_defaults/v2/` 模板子树。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| `/config/` 整体被忽略，新模板无法被 git 追踪 | 高 | 仅放行 `_defaults/v2`，不放行真实 persona 或 config.json |
| YAML 默认模板与未来 spec 不一致 | 中 | README 标注 v2.1-proposal，后续 P1/P2 对齐 |
| 误提交真实 `config/persona/<id>` | 中 | `.gitignore` 只加 `_defaults/v2` 例外，draft/pending/frozen 仍显式忽略 |

**完成后回填**

已完成。

- 实际改动：
  - 新增 `config/persona/_defaults/v2/guard.yaml`
  - 新增 `config/persona/_defaults/v2/eval.yaml`
  - 新增 `config/persona/_defaults/v2/trace.yaml`
  - 新增 `config/persona/_defaults/v2/README.md`
  - 修改 `.gitignore`：继续忽略真实 `/config/`，仅放行 `config/persona/_defaults/v2/**`，并显式忽略 `.draft/`、`source.frozen.md`、`_pending_freeze/`
- 验证证据：
  - `ls -la config/persona/_defaults/v2` 显示 `README.md`、`eval.yaml`、`guard.yaml`、`trace.yaml`。
  - `git check-ignore -v config/config.json` 命中 `config/*`，确认真实配置仍忽略。
  - `git check-ignore -v config/persona/_defaults/v2/guard.yaml` 命中 `!config/persona/_defaults/v2/**`，确认模板被放行。
  - `git check-ignore -v config/persona/demo-v2/.draft/persona.yaml` 仍命中忽略规则，确认 draft 不会被误提交。
- 遗留风险：`.draft/` / `_pending_freeze/` 当前被 `config/persona/*` 泛规则覆盖，显式规则也已加入；若未来放行更多 persona 子路径，需要重新跑 `git check-ignore`。

### P0-3 拆分 §16 到 `system-module-architecture.md`

**开始前拆分**

1. 从 `docs/tracking/persona-source-importer.md` 中抽出 `## 16. 系统模块体系` 到 `## 17. 首轮确认` 前的全部内容。
2. 新建 `docs/tracking/system-module-architecture.md`，放入抽出的 §16 内容，并补充独立定位说明。
3. 在 importer 原 §16 位置保留短引用块，声明 Part A importer 不依赖 Part B runtime/system module。
4. 保留 importer §17 决策表中的 Q13-Q17，但标注这些决策归属 Part B，后续 P1-5 再补输入来源列。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 大段移动导致章节锚点断裂 | 高 | 移动后用 `rg '^## 16\\.'` 和 `rg 'system-module-architecture'` 验证 |
| §17 对 Q13-Q17 的引用失去上下文 | 中 | 原位置保留引用块；P1-5 再标注 Part B 来源 |
| 误复制而非剪切导致两边重复维护 | 中 | importer 文档只保留单段引用，不保留 §16.* 子节 |

**完成后回填**

已完成。

- 实际改动：
  - 新建 `docs/tracking/system-module-architecture.md`。
  - 将原 importer `## 16. 系统模块体系` 到 `## 17. 首轮确认` 前的内容剪切到新文档。
  - importer 原 §16 位置改为“已拆出”引用块，并声明 Part A importer 不依赖 RuntimeStateBus、26 模块或 compiler。
- 验证证据：
  - `wc -l docs/tracking/persona-source-importer.md docs/tracking/system-module-architecture.md` 输出 importer `1137` 行、架构文档 `658` 行。
  - `rg -n '^###? 16\\.' docs/tracking/persona-source-importer.md docs/tracking/system-module-architecture.md` 显示 importer 仅剩 `## 16. 系统模块体系（已拆出）`，`16.1~16.12` 全在架构文档。
- 遗留风险：§17 决策表 Q13-Q17 仍在 importer 文档中保留，P1-5 需要通过 `输入来源` 列标注其归属 Part B。

### P0-4 统一 Freeze 语义为 `Pending Freeze`

**开始前拆分**

1. 在 importer §0 增加 compiler 前 Freeze 只写 `_pending_freeze/` 的边界声明。
2. 搜索全文 `Freeze` / `freeze` / `config/persona/<id>/`，修正“写正式路径”的表述。
3. 新增 `Pending Freeze vs Schema Freeze` 小节，定义 compiler 前后的不同动作。
4. 确保 `_pending_freeze/` 与 `.draft/`、正式 `config/persona/<id>/` 的关系不再冲突。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 只替换局部导致旧语义残留 | 高 | `rg -n 'Freeze|freeze|_pending_freeze|config/persona/<id>'` 全文扫描 |
| 与 Q6 “freeze 即写运行时” 决策冲突 | 中 | Q6 改成“Schema Freeze 才写运行时；Pending Freeze 需二次确认但不写运行时” |
| UI 章节仍暗示首版按钮可写正式路径 | 中 | P2-2 再删除首版 UI 承诺，本步先修动作语义 |

**完成后回填**

已完成。

- 实际改动：
  - importer §0 增加 compiler dry-run 前只能执行 Pending Freeze 的边界声明。
  - §7.3 / §7.4 UI 草案中的按钮与动作改为 `Pending Freeze to _pending_freeze/`。
  - §9 draft 隔离不变量改为：draft 不进正式运行时，compiler 前只可 Pending Freeze 到 `_pending_freeze/`。
  - §10 S5/S6/S7 将 freeze 分成 Pending Freeze 与 Schema Freeze。
  - §11 新增 `Pending Freeze vs Schema Freeze` 对照表。
  - §13 / §17 中 Q6 改为：compiler 前 freeze = Pending Freeze，仅暂存；Schema Freeze 写运行时前必须二次确认。
- 验证证据：
  - `rg -n 'Pending Freeze|Schema Freeze|_pending_freeze' docs/tracking/persona-source-importer.md` 可定位统一语义。
  - 正文不再把 compiler 前 freeze 描述为写入正式运行时目录；旧冲突只保留在 §18/§19 审计记录中作为历史问题。
- 遗留风险：P2-2 仍需把首版 UI 交互承诺降级为后续 S10'，避免读者误以为首版包含按钮。

### P0-5 明确首版 15 文件 skeleton 输出

**开始前拆分**

1. 修改 importer §5.1 流水线图：输出从 `.draft/*.yaml（12 个）` 改为 `15 files + modules placeholder + _import_report.json`。
2. 修改 §5.4 默认模板描述：首版默认模板只有 guard/eval/trace；其余工程文件生成空 skeleton。
3. 增加 `首版 draft 输出矩阵`，明确 6 创作型、3 默认模板、6 空 skeleton、modules 占位。
4. 确认 §2 目标和 §4 目录结构不再与 §5 冲突。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| “15 文件 skeleton”被误解为字段完整 | 中 | 明确 partial draft，空 skeleton 只含 schema/version/status/TODO |
| modules 占位被误解为创建 26 个 module.yaml | 中 | 首版只生成 `modules/_README.md`，不生成实例 |
| 与 Q8 “仅 guard/eval/trace”冲突 | 低 | 写明 Q8 指默认模板覆盖范围，不是 draft 文件数量 |

**完成后回填**

已完成。

- 实际改动：
  - importer §5.1 流水线输出从 `.draft/*.yaml（12 个）` 改为 `.draft/*.yaml（15 个，partial skeleton）` + `.draft/modules/_README.md` + `_import_report.json`。
  - 新增 §5.1.1 `首版 draft 输出矩阵`，列出 6 创作型、3 默认模板、6 空 skeleton 和 modules 占位。
  - §5.4 改为 `默认模板（首版 3 文件 + 6 个工程 skeleton）`，明确 Q8 只限制默认模板覆盖范围，不限制 draft 文件数量。
- 验证证据：
  - `rg -n '15 个|首版 draft 输出矩阵|partial skeleton' docs/tracking/persona-source-importer.md` 能定位新合同。
  - `rg -n '12 个|工程型 6 文件|config/runtime\\.toml' docs/tracking/persona-source-importer.md` 仅在审计历史或已转成后续风险项中出现，不再出现在 §5 合同正文。
- 遗留风险：§3 仍把部分工程文件描述为“默认模板”，P2-5 会进一步拆出“运维配置/工程 skeleton”第三类，减少读者误解。

### P1-1 API 前缀对齐 `/api/admin/persona/*`

**开始前拆分**

1. 全文替换实施切片中的 `/api/persona/*` 为 `/api/admin/persona/*`。
2. 保留 §18 审计历史中的旧路径描述，作为风险记录，不改历史结论。
3. 在 §10 S5 增加“经 `admin/routes/api/__init__.py` 的 `/api/admin` 前缀聚合”说明。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 误改审计历史，丢失问题来源 | 低 | 只改方案合同区，审计历史保留 |
| 漏改 S5/S10' 两处 API 表述 | 中 | `rg -n '/api/persona|/api/admin/persona'` 全文扫描 |

**完成后回填**

已完成。

- 实际改动：importer §10 S5 API 路径改为 `POST /api/admin/persona/import`、`GET /api/admin/persona/draft/<id>`、`POST /api/admin/persona/freeze/<id>`，并注明经 `/api/admin` 聚合路由挂载。
- 验证证据：
  - `rg -n '/api/persona|/api/admin/persona' docs/tracking/persona-source-importer.md` 只在 S5 合同区出现 `/api/admin/persona/*`；旧 `/api/persona/*` 仅保留在 §18 审计历史。
- 遗留风险：真正实现 `admin/routes/api/persona_importer.py` 时仍需在 `admin/routes/api/__init__.py` include router，本轮不写代码。

### P1-2 改为 `persona_import` LLMTask/profile

**开始前拆分**

1. 将 importer 文档中“固定 `claude-haiku-4-5` / 锁定 model id”的首版合同改为 `task='persona_import'`。
2. 增加配置示例：`llm.task_profiles.persona_import` 指向可用 profile，默认建议模型可以是 Claude Haiku，但不硬编码。
3. 更新 Q3 决策：从“模型 id”调整为“默认推荐 profile，可由配置覆盖”。
4. 保留审计历史中关于硬编码的批评，不改历史记录。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 文档写 `persona_import` 但当前代码 LLMTask 尚未包含 | 中 | 明确这是实施前合同，真正代码实现时需新增 LLMTask |
| 失去原“便宜快”的模型建议 | 低 | 保留为默认推荐 profile，不作为硬编码 |
| 多处 `claude-haiku-4-5` 残留 | 中 | `rg -n 'claude-haiku-4-5|锁定 model id|persona_import'` 扫描 |

**完成后回填**

已完成。

- 实际改动：
  - §2 非目标改为：抽取走现有 LLM client，并使用 `persona_import` task profile。
  - §5.2 增加 `LLMRequest(task="persona_import", requires_capabilities=("json",))` 合同与配置示例。
  - §8 guard 默认模板示例从 `judge_model` 改为 `task_profile: persona_import`。
  - §13 / §17 Q3 从“固定 Claude Haiku 模型 id”改为“新增 `persona_import` task profile，模型由配置选择”。
  - §17.2 修订清单同步改为 task profile。
- 验证证据：
  - `rg -n 'claude-haiku-4-5|锁定 model id|persona_import|task_profile' docs/tracking/persona-source-importer.md config/persona/_defaults/v2/guard.yaml` 显示合同区使用 `persona_import`；`claude-haiku-4-5` 仅在配置示例和审计历史中出现。
- 遗留风险：真正代码实现时仍需在 `services/llm/llm_request.py` 增加 `persona_import` 到 `LLMTask`，本轮未改运行时代码。

### P1-3 draft 元数据外挂到 `_import_report.json`

**开始前拆分**

1. 修改 §5.3：draft YAML 不再保留 `{value, source_span, confidence, extractor}` 包装。
2. 增加 `_import_report.json` 结构示例，字段包括 `file`、`key_path`、`source_span`、`confidence`、`extractor`、`default_used`、`issue_level`。
3. 修改 admin 高亮描述：从 `_import_report.json` 读取高亮信息。
4. 保留审计历史中“内嵌元数据冲突”的问题记录。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| YAML 与 report 分离后 UI 定位复杂 | 中 | report 使用 `file + key_path` 稳定指向 YAML 字段 |
| 失去字段级溯源直观性 | 低 | report 作为唯一溯源源，YAML 保持 compiler 可消费 |
| 未同步 §7 高亮描述 | 中 | 全文扫描 `source_span` 与 `admin UI` |

**完成后回填**

已完成。

- 实际改动：
  - §2 目标改为：draft 产出纯 YAML，字段溯源写入 `_import_report.json`。
  - §5.3 改为 `抽取产物：纯 YAML + _import_report.json`，示例 YAML 不再内嵌 `source_span/confidence/extractor` 包装。
  - `_import_report.json` 示例加入 `file`、`key_path`、`source_span`、`confidence`、`extractor`、`default_used`、`issue_level`。
  - §7.4 高亮说明改为从 `_import_report.json.fields[].source_span` 读取。
- 验证证据：
  - `rg -n 'draft YAML|source_span|confidence|extractor|_import_report\\.json' docs/tracking/persona-source-importer.md` 显示 §5.3 已明确纯 YAML 约束，旧冲突只保留在审计历史。
- 遗留风险：§7.3 UI 草案仍显示 `Draft (12)`，P2-2/P0-5 的后续收口需要把 UI 草案降级或改为 15。

### P1-4 hard_rule 三类 enforce 拆分

**开始前拆分**

1. 修改 §7.2 引用闭环检查，新增 hard_rule enforce 分类。
2. 修改 §9 不变量，不再要求每条 hard_rule 都能映射到 `guard.hard_check.patterns`。
3. 增加三类目标：
   - `pattern_guardable` -> `guard.hard_check.patterns`
   - `judge_guardable` -> `guard.soft_judge`
   - `eval_only` -> `eval.critical_failure` / eval cases
4. 标明 compiler 未上线前，`judge_guardable` 和 `eval_only` 只能做结构校验或 warn。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 降低 hard_rule 守门强度 | 中 | 要求每条 hard_rule 必须显式分类，不能无归属 |
| 与 eval 全覆盖不变量重复 | 低 | eval_only 与 critical_failure/eval cases 关联，pattern/judge 仍可同步进 eval |
| 未同步风险表和审计历史 | 低 | 审计历史保留原问题，合同区更新 |

**完成后回填**

已完成。

- 实际改动：
  - §7.1 error 含义改为 hard_rule 未标注 enforce 分类。
  - §7.2 引用闭环新增 `pattern_guardable`、`judge_guardable`、`eval_only` 三类检查。
  - §9 不变量改为 hard_rule 分类强制 + 下游覆盖分级；compiler 未上线前，judge/eval 覆盖缺失可降级 warn，Schema Freeze 前必须补齐。
- 验证证据：
  - `rg -n 'pattern_guardable|judge_guardable|eval_only|Schema Freeze 前' docs/tracking/persona-source-importer.md` 定位新规则。
  - `hard_rule 可机器化` 仅保留在审计历史/旧问题描述中，不再作为合同区不变量。
- 遗留风险：source.md 模板还没有给用户展示如何标注 enforce 分类，后续 P2-4 / P2-6 可补最小模板和字段映射列。

### P1-5 §17 决策表补 `输入来源` 列

**开始前拆分**

1. 给 §17.1 决策表新增第 5 列 `输入来源`。
2. Q1~Q6 标注 `用户选择 / 首轮确认` 或 `审计整改后修订`。
3. Q7~Q12 标注 `方案推荐 + 用户确认` 或 `审计交集`。
4. Q13~Q17 标注 `Part B / SystemModule 架构`，避免误当 importer 首版合同。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| Markdown 表太宽 | 低 | 仍保留单行表，优先追溯性 |
| 来源分类争议 | 中 | 用保守标签，不把审计后修订伪装成原始用户选择 |
| Q13-Q17 仍留在 importer 文档 | 中 | 明确标 Part B，P1-6 再同步收口 |

**完成后回填**

已完成。

- 实际改动：
  - §17.1 决策表新增 `输入来源 / 归属` 列。
  - 表前增加“2026-05-24 用户确认：按下表当前建议执行”。
  - Q1~Q8 标注 Part A / 审计整改归属；Q13~Q17 标注 Part B 或 Part B 接口。
- 验证证据：
  - 脚本统计 §17.1 中 `| Q` 行数为 `17`。
  - `rg -n '输入来源 / 归属|用户确认：按下表' docs/tracking/persona-source-importer.md` 定位表头与确认说明。
- 遗留风险：Q13~Q17 仍在 importer 文档保留为索引，P1-6 需要进一步强调 Part A/Part B 边界。

### P1-6 importer 文档同步收口

**开始前拆分**

1. 将文首 / §0 / §3 的“详见 §16.8”改为 v2.1 spec + system-module architecture 双引用。
2. 明确 Part A 首版边界：`source.md -> 15 文件 partial draft + modules/_README.md + _import_report.json + CLI`。
3. 将 §17.4 的 S1' 实现前置条件改为：P0/P1/P2 全部完成后才能启动 Part A S1。
4. 将旧的 §16.8 引用改为 `system-module-architecture.md §16.8` 或 `persona-spec-format.md v2.1`。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 引用改太多导致旧审计历史失真 | 低 | 只改合同区，审计历史保留 |
| Part A/Part B 边界仍有混杂 | 中 | `rg '§16\\.8|S1\\''` 扫描合同区 |
| 误把 implementation 发令写成已启动 | 低 | 明确“本轮不启动运行时代码实现” |

**完成后回填**

已完成。

- 实际改动：
  - 文首适用范围改为 Persona Spec v2.1，并链接 `persona-spec-format.md` v2.1 扩展。
  - §1.1 将完整 15 文件清单来源改为 `persona-spec-format.md v2.1`，SystemModule runtime 细节改指 `system-module-architecture.md`。
  - §4.1 中 `modules/<id>/module.yaml` 改为首版 `modules/_README.md` 占位。
  - §17.2 / §17.4 同步标注 spec 扩容和 §16 拆分已完成，Part A S1 需 P0/P1/P2 全部完成后另行发令。
- 验证证据：
  - `rg -n '§16\\.8|S1\\'|system-module-architecture|persona-spec-format.md.*v2\\.1' docs/tracking/persona-source-importer.md` 显示合同区已改为 v2.1 / Part B 引用；旧 `§16.8` 只保留在 §15/§18/§19 审计历史。
- 遗留风险：P2-5 还需把 §3 的“创作/工程”二分类细化，避免运维配置混入 source.md。

### P2-1 默认模板出处更正

**开始前拆分**

1. 搜索 `config/runtime.toml`、`from kernel.config import BotConfig`、`默认模板` 相关旧说法。
2. 合同区统一改为 `kernel/config.py + config/config.json` 或 `_defaults/v2` 文件。
3. 审计历史保留旧问题，不回写。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 把审计历史也改掉 | 低 | 只改合同区/风险表 |
| 默认模板与运行时投影混淆 | 中 | 区分 `_defaults/v2` 文件与未来 runtime 投影 |

**完成后回填**

已完成。

- 实际改动：
  - §11 与现有系统关系改为：首版 importer 不改 BotConfig / `kernel/config.py` / `config/config.json`；后续 runtime skeleton 补全只读投影。
  - §12 风险表从“默认模板与 BotConfig 漂移”改为“默认模板与运行时配置漂移”，并说明首版仅 `_defaults/v2` 三份模板。
- 验证证据：
  - `rg -n 'config/runtime\\.toml|from kernel.config import BotConfig|默认模板与 BotConfig|默认模板与运行时配置' docs/tracking/persona-source-importer.md` 显示旧出处只保留在审计历史，合同区使用新表述。
- 遗留风险：后续实现 runtime skeleton 投影时仍需补真实一致性测试。

### P2-2 删除已延后的 UI 首版承诺

**开始前拆分**

1. 将 §7.3 / §7.4 标题和说明标注为 S10' 后续设计草案，不属于首版验收。
2. 将 §10 S6 保持“后续 UI”语义，首版 S1-S5 + CLI/report 为主。
3. 修正 UI 草案中 `Draft (12)` 为 `Draft (15 skeleton)`，避免与 P0-5 冲突。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 删除 UI 草案导致后续前端缺参考 | 低 | 不删除，只明确后续 S10' |
| 首版验收仍含 UI smoke | 中 | §10 S6 标注“后续，不属于 Part A 首版” |

**完成后回填**

已完成。

- 实际改动：
  - §7.3 标题改为 `admin UI 高亮草案（S10' 后续）`。
  - UI 示例 `Draft (12)` 改为 `Draft (15 skeleton)`。
  - §7.4 增加说明：这些交互不属于 Part A 首版验收，首版只要求 CLI + `_import_report.json` 暴露同等信息。
  - §10 S6 标注为后续 S10'，不属于 Part A 首版验收。
- 验证证据：
  - `rg -n 'Draft \\(12\\)|S10' 后续|Part A 首版|UI smoke|admin SPA 高亮' docs/tracking/persona-source-importer.md` 未再出现 `Draft (12)`，并能定位后续标注。
- 遗留风险：S6 仍保留在切片表中作为后续项；实现排期时需确保 Part A S1-S5 不依赖 S6。

### P2-3 补 `.gitignore` 物理护栏

**开始前拆分**

此项已在 P0-2 因 `/config/` 整体忽略风险提前执行。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 放行 `_defaults/v2` 时误放行真实配置 | 高 | 已用 `git check-ignore` 验证 `config/config.json` 仍忽略 |
| draft / pending freeze 被误提交 | 高 | 已显式加入 ignore，并验证 demo 路径仍忽略 |

**完成后回填**

已完成。

- 实际改动：`.gitignore` 放行 `config/persona/_defaults/v2/**`，显式忽略 `config/persona/*/.draft/`、`source.frozen.md`、`_pending_freeze/`。
- 验证证据：
  - `git check-ignore -v config/config.json` 命中 ignore。
  - `git check-ignore -v config/persona/_defaults/v2/guard.yaml` 命中放行规则。
  - `git check-ignore -v config/persona/demo-v2/.draft/persona.yaml` 仍忽略。
- 遗留风险：未来若放行 `config/persona/<id>-v2/source.md`，必须重新检查不会放行 `.draft/`。

### P2-4 新增最小 `source.md` 模板

**开始前拆分**

1. 在 §4.2 前新增 `§4.2 最小 source.md 模板`。
2. 将原完整模板改为 `§4.3 进阶 source.md 模板`。
3. 最小模板只覆盖 front matter、是谁、怎么说话、知道什么、例子四块必填内容。
4. 后续小节编号顺延，尽量避免大范围重编号。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 最小模板字段不足导致 importer error | 中 | 标明“可通过 CLI 生成 warn/error report 后补齐” |
| 与完整模板重复 | 低 | 完整模板改称进阶模板 |
| 章节编号变动影响引用 | 中 | 保持原内容主体，只插入最小模板并将原标题改名 |

**完成后回填**

已完成。

- 实际改动：
  - §4.2 新增最小 `source.md` 模板，覆盖 front matter、身份、表达、知识边界、少量例子。
  - 最小模板中的 hard_rules 示例补 `enforce: pattern_guardable/judge_guardable/eval_only`。
  - 原 §4.2 长模板改名为 §4.3 进阶模板，并将 `version_hint` 改为 `2.1.0`。
  - `_pending_freeze/` 注释从 `S11'` 改为 compiler 未上线前 Pending Freeze。
- 验证证据：
  - `rg -n '最小 source.md|进阶 source.md|version_hint: 2\\.0\\.0|S11'|enforce:' docs/tracking/persona-source-importer.md` 显示新增最小模板、无合同区 `2.0.0` 残留。
- 遗留风险：字段映射表仍没有“缺失时行为”，P2-6 会补。

### P2-5 新增“运维配置”第三类

**开始前拆分**

1. 修改 §3 标题与分类表，从 `6 创作 + 9 工程 + per-module` 改为 `6 创作 + 3 默认模板 + 6 skeleton + 运维配置`。
2. 将 RRF、packing guard、per-group at_only/sticker_mode、切段上限等列为运维配置，不进 source.md。
3. 保留 Part B `modules/` 作为接口/占位，不作为 Part A 抽取目标。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 与 §15 注入源审计“全部漏掉”冲突 | 中 | 区分“运行时确实影响回复”和“是否由 source.md 抽取” |
| 工程文件 skeleton 与运维配置边界模糊 | 中 | 表中增加“Part A 首版动作”列 |

**完成后回填**

已完成。

- 实际改动：
  - §3 标题改为 `6 创作 + 3 默认模板 + 6 skeleton + 运维配置`。
  - 分类表增加 `Part A 首版动作` 列。
  - RRF 权重、packing guard、per-group `at_only` / `sticker_mode`、切段上限等归为运维配置，不进 source.md，不由 importer 抽取。
  - 明确 modules 首版只生成 `_README.md` 占位，完整 26 个 module.yaml 属于 Part B。
- 验证证据：
  - `rg -n '6 创作 \\+ 9 工程|运维配置|工程 skeleton|Part B 接口|importer Part A 只对' docs/tracking/persona-source-importer.md` 显示旧标题已消失，新分类已生效。
- 遗留风险：§6 字段映射表仍未标缺失行为，P2-6 继续补。

### P2-6 字段映射表补“缺失时行为”列

**开始前拆分**

1. 将 §6 字段映射表表头从 4 列改为 5 列。
2. 为每行补 `error` / `warn_default` / `silent_default`。
3. 明确定义三种行为。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 表格行较多，手工补列漏行 | 中 | 用局部替换并人工核对每行列数 |
| 选填字段到底 warn 还是 silent 有争议 | 中 | 有默认值 silent，影响质量但可空 warn |

**完成后回填**

已完成。

- 实际改动：
  - §6 字段映射表新增 `缺失时行为` 列。
  - 所有必填字段缺失行为为 `error`。
  - 所有选填字段当前为 `silent_default`。
  - 表后新增 `error` / `warn_default` / `silent_default` 三种行为定义。
- 验证证据：
  - 脚本检查 §6 表格中 30 行均为 5 列，无坏行输出。
  - `rg -n '缺失时行为|warn_default|silent_default|\\| source 章节' docs/tracking/persona-source-importer.md` 定位新增列与定义。
- 遗留风险：当前没有字段使用 `warn_default`；实现时可根据用户体验再把“表达素材不足”等数量类问题放到 validator warn。

### P2-7 v2 schema 标注 proposal-level

**开始前拆分**

1. 在文首增加 proposal-level 声明。
2. 明确最终 schema 以 `persona-spec-format.md` v2.1+ 为准。
3. 明确 importer 实现使用松 schema（unknown keys allowed），避免 spec 迭代时锁死。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| proposal-level 让实现者误以为不用校验 | 中 | 同时说明以 v2.1+ 为准，松 schema 不等于无 schema |
| 与已创建默认模板冲突 | 低 | 默认模板也标 `2.1.0-proposal` |

**完成后回填**

已完成。

- 实际改动：
  - 文首新增 `Schema 状态`，说明 v2.1 schema 是 proposal-level。
  - 明确最终字段以 `persona-spec-format.md` v2.1+ 为准。
  - 明确 Part A 实现使用松 schema（unknown keys allowed + 必填字段校验）。
- 验证证据：
  - `rg -n 'proposal-level|松 schema|unknown keys|2\\.1\\.0-proposal' docs/tracking/persona-source-importer.md config/persona/_defaults/v2/*.yaml` 能定位主文档声明与默认模板 proposal 版本。
- 遗留风险：松 schema 策略需要在真正 validator 实现时变成测试用例，避免变成“无校验”。

### P2-8 膨胀控制条款

**开始前拆分**

1. 在主方案末尾追加维护纪律。
2. 设定 importer 主文档行数软上限和拆分规则。
3. 要求新增内容优先进入执行追踪 / 架构文档 / 迁移文档，不再无限追加主方案。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 行数上限过硬影响必要审计记录 | 低 | 设为软上限，超过则必须说明拆分理由 |
| 新增维护纪律本身又增加文档长度 | 低 | 只加短节 |

**完成后回填**

已完成。

- 实际改动：
  - importer 主方案末尾新增 `## 20. 维护纪律（2026-05-24 收口）`。
  - 明确主方案只保留 Part A 稳定合同、审计摘要和关键决策索引。
  - 明确 Runtime/SystemModule 写入 `system-module-architecture.md`，执行过程写入本文档，迁移表写入 `docs/migrations/persona-v2-importer.md`。
  - 设定 importer 主文档软上限 1500 行，并要求新增字段同步回答来源、缺失时行为、是否属于 Part A 首版。
- 验证证据：
  - `wc -l docs/tracking/persona-source-importer.md` 输出 `1330`，低于 1500 行软上限。
  - `rg -n "维护纪律|1500 行|persona-v2-importer.md" docs/tracking/persona-source-importer.md` 命中 §20 维护纪律和迁移文档路径。
- 遗留风险：主方案仍含两份审计历史和若干长表，后续进入 S1/S2 时若继续膨胀，需要优先拆到迁移文档或执行追踪，而不是继续追加主文档。

## 4. 收口验证

**开始前拆分**

1. 同步整改总表，把 P0/P1/P2 的状态从待开始改为已完成。
2. 建立 `docs/migrations/persona-v2-importer.md`，统一主方案与整改文档对迁移清单路径的引用。
3. 在 `maintenance-log.md` 追加本次 P0/P1/P2 收口里程碑。
4. 运行最终 grep / ignore / git 状态检查，只验证文档与配置护栏，不跑 runtime 测试。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 收口文档再次变成长篇流水账 | 中 | remediation 总表只写摘要，细节留在本文执行日志 |
| 迁移清单被误读为代码迁移已完成 | 中 | 明确当前仅完成文档/config 迁移，API/UI/runtime 仍未实现 |
| git 状态里混有既有前端修改 | 中 | 最终报告只声明本轮触碰的 persona docs/config/maintenance，不接管无关 frontend 改动 |

**完成后回填**

已完成。

- 实际改动（P0/P1/P2 收口时的历史记录）：
  - `docs/tracking/persona-source-importer-remediation.md` 当时状态更新为 `v0.3 收口版`，实施日志标记 P0/P1/P2 全部完成。
  - `docs/tracking/persona-source-importer.md` 当时文首状态更新为 `v0.3 收口版（P0/P1/P2 已完成，等待 Part A S1 发令）`。
  - 新增 `docs/migrations/persona-v2-importer.md`，记录文档/config 已迁移项与待代码实现切片。
  - `maintenance-log.md` 追加 `Persona Source Importer P0/P1/P2 整改收口` 条目。
- 验证证据：
  - `rg -n "待整改|P0~P2 整改完成前|persona-v2-importer-split" ...` 无命中。
  - `wc -l docs/tracking/persona-source-importer.md ...` 显示 importer 主文档 `1330` 行，迁移文档 `38` 行。
  - `rg -n "v0.3 收口版|P0/P1/P2 已完成|等待 Part A S1" ...` 当时命中主方案、整改总表和执行追踪三处状态。
  - `git check-ignore -v config/config.json config/persona/_defaults/v2/guard.yaml config/persona/demo-v2/.draft/persona.yaml` 确认真实配置仍忽略、默认模板被放行、draft 仍忽略。
- 遗留风险：
  - 当前仓库还有既有 admin/frontend 修改，本次收口不接管也不回滚。
  - `$HOME/OmubotWorkspace/omubot` 当前不是 git 仓库，本轮按可用 git 根目录 `/Volumes/OmubotDisk/omubot` 完成；若后续切回 AGENTS 建议路径，需要先修复/挂载正确 workspace。
  - 未运行 pytest/pyright/vue-tsc；本轮只做文档和 config ignore 护栏验证。

## 5. Part A 实施追踪

### A0 实施启动与范围确认

**开始前拆分**

1. 读取 importer 主方案 §10 S1-S5、迁移清单和现有 LLM/API/CLI 代码入口。
2. 确认本轮实现范围：`services/persona` importer 包、CLI、`persona_import` LLMTask/profile 同步、`/api/admin/persona/*` admin API、单元/接口测试。
3. 明确不做范围：v2 compiler、SystemModule/RuntimeStateBus、admin SPA 双栏高亮、正式 Schema Freeze 写运行时路径。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| `persona_import` 加入 LLMTask 后破坏前端/admin 同步守门 | 高 | 同步 `ProviderTaskKey`、`providerTaskOrder`、`providerTaskLabels`、LLM pipeline |
| importer 写入真实 runtime 配置 | 高 | 首版只写 `.draft/` 和 `_pending_freeze/`；正式路径不写 |
| 真实 LLM 不可用导致首版不可测 | 中 | 确定性 parser/writer 可独立运行；LLM 抽取器只提供可 mock 边界 |
| 现有仓库有无关 admin/frontend 修改 | 中 | 只触碰 system provider task 同步所需最小前端文件，不回滚无关变更 |

**完成后回填**

已完成。

- 实际改动：
  - 确认本轮进入 Part A S1-S5 实现，范围限于 backend/CLI/API。
  - 明确不实施 v2 compiler、SystemModule/RuntimeStateBus、admin SPA 双栏高亮和正式 Schema Freeze。
  - 识别 `persona_import` 会触发 LLMTask/admin provider/pipeline 同步守门，纳入 A3。
- 验证证据：
  - `rg -n "S1|S2|S3|S4|S5|persona_import|/api/admin/persona" docs/tracking/persona-source-importer.md` 已定位实施合同。
  - 已读取 `services/llm/llm_request.py`、`services/llm/llm_pipelines.py`、`admin/routes/api/__init__.py`、provider 面板同步测试。
- 遗留风险：本轮仍不处理 admin SPA；前端只同步 System Provider 的 task 类型/标签，不新增 Persona UI。

### A1 S1 source parser + importer package scaffold

**开始前拆分**

1. 新建 `services/persona/` 包，放置数据模型、source parser、draft builder、writer、CLI 入口。
2. parser 支持 YAML front matter、H1/H2/H3 切章、列表/键值/行号 span 提取。
3. 建立 15 文件 skeleton 的常量清单，保证 `.draft/*.yaml` 输出面稳定。
4. 增加 parser 单元测试，覆盖最小 `source.md` 模板和缺 front matter 的 error。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| Markdown 解析器过度复杂 | 中 | 首版只实现标题/列表/冒号键值/行号，不引入新依赖 |
| 中文章节标题有多种写法导致漏抽 | 中 | 按方案最小模板支持，未匹配字段进入 report issue |
| 行号 span 不准影响 UI 高亮 | 中 | parser 保留原始行号，测试断言关键字段 span |

**完成后回填**

已完成。

- 实际改动：
  - 新增 `services/persona/__init__.py`、`models.py`、`parser.py`、`builder.py`、`writer.py`、`importer.py`。
  - parser 支持 YAML front matter、标题切章、bullet/numbered list、冒号键值、原始行号 span。
  - 建立 `DRAFT_YAML_FILES` 15 文件清单和 `<id>-v2` namespace 规范化。
- 验证证据：
  - `tests/test_persona_importer.py::test_parse_source_markdown_frontmatter_and_sections` 覆盖 front matter、章节和 source hash。
  - 相关测试组合 `19 passed`。
- 遗留风险：Markdown parser 首版只支持合同模板的轻量语法，复杂表格/嵌套列表会进入 report issue 或后续增强。

### A2 S2 deterministic extractors + defaults + draft writer

**开始前拆分**

1. 用 parser 结果填充 persona/voice/knowledge/examples 的确定性字段。
2. 从 `config/persona/_defaults/v2/` 复制 guard/eval/trace。
3. 为 runtime/capabilities/adapter/relationships/memory/state/thinker/system 生成 schema skeleton。
4. 写 `_import_report.json`：fields、issues、source_hash、generated_files、default_used。
5. 测试确认 15 YAML、`modules/_README.md`、report 全部落盘。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 缺必填字段时还写出看似可用 draft | 高 | `strict=True` 时阻止写盘；API/CLI 可用 `strict=False` 生成 report 供修复 |
| 默认模板路径受 `.gitignore` 影响 | 低 | 使用 repo 内物理路径读取，测试用临时 root |
| YAML 字段顺序不稳定 | 低 | 使用 dict 插入顺序和 `sort_keys=False` |

**完成后回填**

已完成。

- 实际改动：
  - deterministic builder 填充 `persona.yaml`、`voice.yaml`、`knowledge.yaml`、`examples.yaml` 的基础字段。
  - `guard.yaml`、`eval.yaml`、`trace.yaml` 从 `config/persona/_defaults/v2/` 复制。
  - runtime/capabilities/adapter/relationships/memory/state/thinker/system 生成 proposal skeleton。
  - writer 写入 `.draft/`、`.draft/modules/_README.md` 和 `_import_report.json`。
  - `.gitignore` 放行 `config/persona/*/source.md`，继续忽略 `.draft/`、`source.frozen.md`、`_pending_freeze/`。
- 验证证据：
  - `tests/test_persona_importer.py::test_persona_importer_writes_15_yaml_files_and_report` 断言 15 YAML + report 落盘。
  - `git check-ignore -v config/persona/demo-v2/source.md ...` 确认 source 放行、draft/pending/frozen 仍忽略。
- 遗留风险：表达素材/examples 复杂结构首版仍是保守抽取或 stub，后续可用 LLM extractor 增强。

### A3 S3 persona_import LLM task/profile 边界

**开始前拆分**

1. `services/llm/llm_request.py` 增加 `persona_import`。
2. 同步 admin provider TS 类型、展示顺序、中文标签和 `services/llm/llm_pipelines.py`。
3. importer LLM 抽取器构造 `LLMRequest(task="persona_import", requires_capabilities=("json",))`，提供 mockable 调用边界。
4. 测试覆盖 LLMTask 同步守门和 source_span 缺失丢弃。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 新 task 未配置导致运行时 fallback 不明确 | 中 | BotConfig validator 默认映射到 default profile；capability 不足由 `_call` fail-fast |
| 前端 provider 面板测试受既有未提交改动影响 | 中 | 只做最小同步，不重构 provider UI |
| LLM JSON 返回格式脏 | 中 | 解析器接受 dict/list，失败写 warn issue，不阻断确定性 draft |

**完成后回填**

已完成。

- 实际改动：
  - `services/llm/llm_request.py` 增加 `persona_import`。
  - `kernel/config.py` 默认把 `persona_import` 路由到同名 profile 或 default profile。
  - `services/llm/llm_pipelines.py` 将 `persona_import` 纳入 learning pipeline。
  - `admin/frontend/src/views/system/helpers/types.ts`、`SystemProviders.vue` 同步 task key、顺序、中文标签和 JSON capability 提示。
  - 新增 `services/persona/llm_extractor.py`，构造 `LLMRequest(task="persona_import", requires_capabilities=("json",))`，并提供 source_span 过滤。
- 验证证据：
  - `tests/test_llm_task_admin_sync.py` 3 项通过。
  - `tests/test_llm_pipelines.py` 8 项通过。
  - `tests/test_persona_importer.py::test_persona_llm_extractor_uses_persona_import_task` 覆盖 request task/capability。
- 遗留风险：真实 provider 是否具备 JSON capability 取决于当前配置；不足时 spine 会 fail-fast。

### A4 S4 validator + Pending Freeze

**开始前拆分**

1. validator 实现 error/warn/info issue 汇总和 hard_rule enforce 分类检查。
2. Pending Freeze 只复制 `.draft/` 到 `_pending_freeze/`，并复制 `source.md` 为 `source.frozen.md`。
3. 阻止 Schema Freeze 或正式 `config/persona/<id>/` 写入。
4. 测试覆盖缺字段 error、hard_rule enforce 缺失、pending freeze 文件布局。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 用户以为 freeze 已进入运行时 | 高 | API/CLI 返回 `mode=pending_freeze` 和明确路径 |
| pending freeze 覆盖旧暂存 | 中 | 先清理目标 `_pending_freeze/` 再复制，report 记录 source_hash |
| validator 太严导致最小模板跑不动 | 中 | import 可 `strict=False` 出 draft/report；pending freeze 必须无 error |

**完成后回填**

已完成。

- 实际改动：
  - builder 校验 required fields 和 hard_rule enforce 三分类。
  - `strict=True` 时存在 error 不写 `.draft/`。
  - Pending Freeze 只复制 `.draft/` 到 `_pending_freeze/`，并复制 `source.md` 为 `source.frozen.md`。
  - Pending Freeze 遇到 report error 会拒绝执行，不写正式 runtime 路径。
- 验证证据：
  - `tests/test_persona_importer.py::test_strict_import_does_not_write_when_required_fields_missing` 通过。
  - `tests/test_persona_importer.py::test_pending_freeze_copies_draft_and_source` 通过。
- 遗留风险：validator 当前覆盖 Part A 必填项和 enforce 分类；完整 v2 schema 校验仍属于 compiler/后续 validator 增强。

### A5 S5 CLI + admin API

**开始前拆分**

1. `python -m services.persona.importer <persona_id>` 支持 `--root`、`--source`、`--strict/--no-strict`、`--pending-freeze`。
2. `admin/routes/api/persona_importer.py` 提供 `POST /persona/import`、`GET /persona/draft/{id}`、`POST /persona/freeze/{id}`，经 `/api/admin` 聚合。
3. API 不实现 admin SPA，只返回 JSON report 和 draft summary。
4. 测试 HTTP round-trip、CLI dry-run、pending freeze。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| API 被未授权访问 | 中 | 路由挂在现有 `/api/admin` 聚合下，沿用 AdminAuthMiddleware；单元测试只验证 router 功能 |
| CLI 默认 root 指向真实 config | 中 | 测试用 `--root tmp_path`；实现中只操作给定 root 下的 persona id |
| GET draft 泄露真实配置 | 中 | 只读取 `.draft/` 和 report，不读取正式 runtime 配置 |

**完成后回填**

已完成。

- 实际改动：
  - CLI：`python -m services.persona.importer <persona_id>` 支持 `--root`、`--defaults`、`--source`、`--strict`、`--no-write`、`--pending-freeze`。
  - API：新增 `admin/routes/api/persona_importer.py`，提供 `POST /persona/import`、`GET /persona/draft/{persona_id}`、`POST /persona/freeze/{persona_id}`。
  - 聚合：`admin/routes/api/__init__.py` include persona importer router，外部路径为 `/api/admin/persona/*`。
- 验证证据：
  - `tests/test_persona_importer.py::test_cli_importer_supports_explicit_root` 通过。
  - `tests/test_persona_importer_api.py::test_persona_importer_api_round_trip` 通过 import/draft/freeze round-trip。
  - 相关测试组合：`19 passed in 0.64s`。
- 遗留风险：API 单元测试验证 router 行为；生产鉴权依赖既有 AdminAuthMiddleware，本轮未新增前端页面。

### A6 最终验证与收口

**开始前拆分**

1. 运行 Python lint，只覆盖 Python 文件。
2. 运行 importer/API/LLM task/pipeline/config 相关 pytest。
3. 运行前端 `vue-tsc --noEmit`，验证 provider task 类型同步。
4. 复查 `.gitignore`：`source.md` 放行，draft/pending/frozen/真实 config 仍忽略。
5. 同步主方案、整改总表、迁移清单和维护日志。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 用 Python ruff 检查 TypeScript/Vue 导致误判 | 低 | Python 与前端验证分开：ruff 只跑 Python，Vue 用 `vue-tsc` |
| 既有 admin/frontend 未提交变更混入最终状态 | 中 | 最终报告单独说明不接管无关学习管线修改 |
| `uv run` 环境 panic 影响验证 | 中 | 记录 uv panic，使用仓库 `.venv/bin/python -m pytest` 验证 |

**完成后回填**

已完成。

- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona admin/routes/api/persona_importer.py admin/routes/api/__init__.py tests/test_persona_importer.py tests/test_persona_importer_api.py services/llm/llm_request.py services/llm/llm_pipelines.py kernel/config.py` 通过。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_importer_api.py tests/test_llm_task_admin_sync.py tests/test_llm_pipelines.py tests/test_config_loader.py` 通过，`43 passed`。
  - `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过。
  - `git check-ignore -v config/persona/demo-v2/source.md ...` 确认 `source.md` 放行，`.draft/`、`_pending_freeze/`、`source.frozen.md`、`config/config.json` 仍按预期忽略。
- 遗留风险：
  - `uv run pytest ...` 在当前 macOS 挂载环境触发 uv `system-configuration` panic，未作为代码失败处理；本轮用 `.venv/bin/python` 完成验证。
  - admin SPA 双栏高亮、v2 compiler / Schema Freeze、RuntimeStateBus/SystemModule 仍未实现。

## 6. S6/S10' admin SPA 实施追踪

### B0 S6/S10' 范围确认与追踪启动

**开始前拆分**

1. 读取 Part A 验收后的追踪状态、迁移清单、现有 persona API、前端路由/菜单和 Calm Ops 组件规范。
2. 确认本阶段只做 admin SPA 可用闭环：在线读取/编辑 `source.md`、触发 import、展示 `_import_report.json`、查看 draft 文件清单、执行 Pending Freeze。
3. 明确不做范围：v2 compiler、Schema Freeze、RuntimeStateBus/SystemModule、正式 runtime persona 路径写入、完整字段级双栏行号跳转高亮。
4. 将后续步骤拆成 B1 API、B2 前端页面、B3 验证收口，并在每一步前后更新本文档。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| admin SPA 误导用户以为 Pending Freeze 已进入正式运行时 | 高 | 页面和 API 只显示/返回 `mode=pending_freeze`、`_pending_freeze/` 路径，不写正式 runtime |
| 在线 source 编辑 API 误写 `.draft/` 或 `_pending_freeze/` | 高 | API 只使用 `PersonaDraftWriter.source_path(persona_id)`，写入 `config/persona/<id>-v2/source.md` |
| 现有未提交 learning/frontend 改动被混入或回滚 | 中 | 只触碰 persona API、persona 新页面、router/menu/docs/log，不清理无关文件 |
| 页面一次性实现过大导致类型/样式不稳 | 中 | 分成 metrics、toolbar、source editor、report panel 四块；优先用 `AppPage`/`MetricCard`/`PageToolbar`/`AppPanelSection` |

**回滚方式**

- B1 可移除 `source` API payload/model/endpoints 与对应 API 测试，不影响已完成 import/draft/freeze。
- B2 可移除 `/persona-importer` 路由、菜单项和 `PersonaImporterView.vue`，后端 Part A API 保持可用。
- B3 文档/维护日志只记录里程碑，可单独 revert 本阶段新增段落。

**验收证据**

- Python：`tests/test_persona_importer_api.py` 覆盖 source read/write + import/draft/freeze。
- Frontend：`cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`。
- Build：若前端类型通过后无阻断，执行 `npm run build` 并记录产物影响；如已有静态脏文件冲突，单独说明。
- 文档：本文、迁移清单与维护日志同步说明 S6/S10' 状态。

**完成后回填**

- 实际改动：
  - 读取 Part A 验收记录、迁移清单、persona importer API、前端路由/菜单、Omubot admin UI 规范与公共组件。
  - 明确本阶段范围为 S6/S10' admin SPA 可用闭环，不启动 v2 compiler / Schema Freeze / RuntimeStateBus / SystemModule。
  - 将实施拆为 B1 source API、B2 前端页面、B3 验证收口。
- 验证证据：
  - `rg -n "S6|S10|source.md|Pending Freeze" docs/tracking/persona-source-importer*.md docs/migrations/persona-v2-importer.md` 已定位上游合同。
  - 已确认当前已有 `/api/admin/persona/import`、`/draft/{persona_id}`、`/freeze/{persona_id}`，缺在线 source 读写 API。
- 遗留风险：
  - 本阶段仍不提供正式 Schema Freeze；页面必须持续标注 Pending Freeze 语义。
  - 工作树中存在无关 learning/frontend/static 改动，本阶段不回滚。

### B1 `source.md` 读写 API + 测试

**开始前拆分**

1. 在 `admin/routes/api/persona_importer.py` 增加 `PersonaSourcePayload`，只接收 `content` 字段。
2. 新增 `GET /api/admin/persona/source/{persona_id}`：读取 `PersonaDraftWriter.source_path(persona_id)`，返回 `ok/persona_id/path/content/exists`；文件不存在时返回空内容和 `exists=false`。
3. 新增 `PUT /api/admin/persona/source/{persona_id}`：创建 `<id>-v2/` 目录并写入 `source.md`，返回写入后的内容长度、路径和 `exists=true`。
4. 增加 API 测试：先 GET 空 source，再 PUT 写入 `MINIMAL_SOURCE`，再 POST import，确认 draft round-trip 仍可用。
5. 运行 `ruff` 与 persona importer API pytest。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| API 写入路径可被 persona_id 注入穿越 | 中 | 沿用 `persona_namespace`，只拼接 `persona_root / <id>-v2 / source.md`；不接受任意 path |
| GET 不存在时返回 404 导致前端无法首屏编辑 | 中 | 返回 `ok=true`、`exists=false`、`content=""`，让前端可直接创建 |
| 写 source 后 draft 旧 report 与 source 不一致 | 中 | 本阶段只提供读写 source；前端保存后标记需重新 import，B2 处理 |
| 测试误依赖生产鉴权 | 低 | 使用 `create_api_router(ctx=tmp)` 的既有 TestClient 模式，只验证路由行为 |

**回滚方式**

- 删除 `PersonaSourcePayload`、`GET/PUT /source/{persona_id}` endpoints 和新增测试断言即可回到 S5 API 面。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check admin/routes/api/persona_importer.py tests/test_persona_importer_api.py`
- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer_api.py`

**完成后回填**

- 实际改动：
  - `admin/routes/api/persona_importer.py` 新增 `PersonaSourcePayload`。
  - 新增 `GET /api/admin/persona/source/{persona_id}`：不存在时返回 `exists=false` 与空内容，方便 admin 首屏创建。
  - 新增 `PUT /api/admin/persona/source/{persona_id}`：只写 `config/persona/<id>-v2/source.md`，自动创建 persona 目录。
  - 为 import/draft/freeze/source 共用 `_valid_namespace`，拒绝空 id、路径分隔符和 `..`。
  - `tests/test_persona_importer_api.py` 新增 source read/write/import round-trip 与路径型 persona id 拒绝测试。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check admin/routes/api/persona_importer.py tests/test_persona_importer_api.py` 通过。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer_api.py` 通过，`3 passed`。
- 遗留风险：
  - 保存 `source.md` 后旧 draft 不会自动失效；B2 前端需把保存后状态标为“需重新导入”。
  - 生产鉴权仍依赖既有 `/api/admin` AdminAuthMiddleware，本测试只覆盖路由行为。

### B2 Admin Persona Importer 页面、路由与菜单

**开始前拆分**

1. 新建 `admin/frontend/src/views/persona/PersonaImporterView.vue`，页面根使用 `AppPage`，复用 `MetricCard`、`PageToolbar`、`AppPanelSection`、`EmptyState`。
2. 页面状态模型：
   - `personaId`：默认 `fengxiaomeng`，可输入切换。
   - `source` / `originalSource`：用于保存按钮 dirty 判断。
   - `draft`：读取 `/api/admin/persona/draft/{id}` 的 summary。
   - `report`：来自 import 或 draft summary 的 `_import_report.json`。
   - loading/saving/importing/freezing/error 状态独立控制。
3. 页面交互：
   - `加载 source`：GET source。
   - `保存 source`：PUT source，并标记需要 re-import。
   - `导入 draft`：POST import 后刷新 draft。
   - `刷新 draft`：GET draft。
   - `Pending Freeze`：用 `NPopconfirm` 二次确认，POST freeze `{confirm:true}`。
4. 视觉结构：
   - 顶部 4 个 `MetricCard`：source 状态、draft 文件数、issue 数、freeze 状态。
   - `PageToolbar` 放 persona id、加载、保存、导入、刷新、Pending Freeze。
   - 主体双栏：左侧 source editor，右侧 report/issues + files。
   - issue/field/file 列表用紧凑行，不使用装饰性卡片或营销说明。
5. 路由与菜单：
   - `admin/frontend/src/router/index.ts` 增加 `/persona-importer`。
   - `SideMenu.vue` 在“日常”下靠近“人设编辑”加入“人设导入”，保留既有 `群聊记忆` 脏改。
6. 运行 `vue-tsc --noEmit`，根据类型错误修正。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 前端页面把 Pending Freeze 描述成正式发布 | 高 | 所有按钮/状态使用 `Pending Freeze`，展示 `_pending_freeze/` 路径，不出现 Schema Freeze |
| 保存 source 后直接 freeze 旧 draft | 高 | 保存后设置 `sourceDirtySinceImport=true`，Pending Freeze 按钮禁用并提示需重新导入 |
| report 字段结构不稳定导致页面崩 | 中 | 对 `issues/fields/generated_files` 做数组归一化和可选字段读取 |
| 新页面样式违反 Calm Ops 约束 | 中 | 使用公共组件和 token，避免渐变/orb/大量 inline style；主内容双栏可响应式堆叠 |
| 修改菜单覆盖既有未提交 label | 中 | 只插入新菜单项，保留 `群聊记忆` |

**回滚方式**

- 删除 `PersonaImporterView.vue`、router `/persona-importer` 条目和 SideMenu 新菜单项即可；B1 后端 API 可独立保留。

**验收证据**

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`
- B3 若类型通过，再跑 `npm run build` 并记录静态产物状态。

**完成后回填**

- 实际改动：
  - 新增 `admin/frontend/src/views/persona/PersonaImporterView.vue`。
  - 页面提供 persona id 输入、source 加载/保存、draft 导入/刷新、Pending Freeze 二次确认。
  - 页面展示 4 个指标：source 状态、draft 文件数、issue 数、Pending Freeze 状态。
  - 主体为左右双栏：左侧 `source.md` 编辑器，右侧 report 的 Issues / Fields / Files tabs。
  - 保存 source 后设置 `sourceDirtySinceImport=true`，在重新 import 前禁用 Pending Freeze。
  - `admin/frontend/src/router/index.ts` 新增 `/persona-importer` 路由。
  - `admin/frontend/src/layouts/components/SideMenu.vue` 在“日常”中新增“人设导入”，并保留既有 `群聊记忆` 菜单文案。
- 验证证据：
  - `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过。
  - `rg -n "Schema Freeze|正式|_pending_freeze|Pending Freeze|sourceDirtySinceImport" admin/frontend/src/views/persona/PersonaImporterView.vue ...` 确认页面只暴露 Pending Freeze 语义。
  - `rg -n "#[0-9A-Fa-f]{3,8}|linear-gradient|radial-gradient|!important|border-radius: (4|20|22|24)" admin/frontend/src/views/persona/PersonaImporterView.vue` 无命中。
- 遗留风险：
  - 首版未实现点击 issue 自动滚动并高亮 source 行；当前仅展示 `source_span` 文本。
  - 页面未做 e2e 浏览器 smoke；B3 先以类型检查和生产构建收口。

### B3 S6/S10' 验证与文档收口

**开始前拆分**

1. 运行 Python ruff，覆盖本阶段触碰的 API 与测试。
2. 运行 persona importer 相关 pytest，确认新增 source API 不破坏 Part A round-trip。
3. 运行 `vue-tsc --noEmit`，确认新增页面、路由、菜单类型干净。
4. 运行 `npm run build`，确认新页面能进入生产产物；如 build 改写 `admin/static`，记录产物状态。
5. 同步 `docs/migrations/persona-v2-importer.md`：S6/S10' 从“后续”更新为“admin SPA 首版已实现”，并保留高亮/e2e 等未做项。
6. 在 `maintenance-log.md` 追加 S6/S10' 里程碑。
7. 更新本文状态和 B3 完成回填。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| build 改写已有未提交 `admin/static` 产物 | 中 | 记录为前端验证产物；不删除无关静态文件 |
| 只跑新增 API 测试漏掉 Part A 回归 | 中 | 跑 `tests/test_persona_importer.py`、`tests/test_persona_importer_api.py` 和 LLM task 同步测试 |
| 文档把“首版页面”夸大成完整 S10' | 中 | 明确已完成 admin SPA 首版闭环，双栏行号跳转高亮/e2e 仍待后续 |
| 工作树已有无关改动影响 git diff 解读 | 中 | 收口报告只列本阶段触碰文件，无关 learning/.research/static 既有改动不归因 |

**回滚方式**

- 后端：revert B1 endpoints/tests。
- 前端：删除 PersonaImporterView、路由、菜单项。
- 文档：revert 本阶段 B0-B3、迁移清单与维护日志条目。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check ...`
- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest ...`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`
- `cd admin/frontend && npm run build`

**完成后回填**

- 实际改动：
  - 完成本阶段 Python lint、pytest、前端类型检查和生产构建。
  - `docs/migrations/persona-v2-importer.md` 状态更新为 S6/S10' admin SPA 首版闭环已完成，Part B 未启动。
  - 迁移清单新增 `## 4. S6/S10' admin SPA 首版迁移`，记录 source API、页面入口、Pending Freeze 交互和未完成的 issue 行号跳转高亮。
  - `maintenance-log.md` 追加 `Persona Source Importer S6/S10' admin SPA 首版落地`。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check admin/routes/api/persona_importer.py tests/test_persona_importer_api.py services/persona tests/test_persona_importer.py services/llm/llm_request.py services/llm/llm_pipelines.py kernel/config.py` 通过。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_importer_api.py tests/test_llm_task_admin_sync.py tests/test_llm_pipelines.py tests/test_config_loader.py` 通过，`45 passed`。
  - `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过。
  - `cd admin/frontend && npm run build` 通过；新增/更新产物包含 `PersonaImporterView-cbtwzOB2.js`、`PersonaImporterView-CE0hmiqa.css`。
- 遗留风险：
  - 首版页面只展示 `source_span` 文本，未实现点击 issue 自动滚动并高亮 source 行。
  - 未运行浏览器 e2e smoke；本轮以 API 测试、类型检查和生产构建验收。
  - 仍未实现 v2 compiler / Schema Freeze / RuntimeStateBus / SystemModule，Pending Freeze 不写正式 runtime persona 路径。
  - 工作树存在本阶段外的 learning/frontend/static/.research 改动，未接管也未回滚。

## 7. Part B Runtime/SystemModule 实施追踪

### C0 Part B S1' 启动与范围确认

**开始前拆分**

1. 读取 `system-module-architecture.md` §16.1~§16.12，确认 Part B 总切片与 S1' 首步合同。
2. 读取当前运行时入口：`kernel/types.py`、`kernel/bus.py`、`kernel/router.py`、`plugins/chat/plugin.py`、现有 prompt/state/memory 注入路径。
3. 确认本轮只启动 S1'：`system.yaml + module.yaml schema + RuntimeStateBus 接口骨架`，不接入现有 chat/prompt 主链路，不替换 plugin/provider/store。
4. 将 S1' 拆成 C1 契约模型与默认 catalog、C2 RuntimeStateBus 与校验、C3 测试/文档收口。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| Part B 范围过大，一步改动主链路导致 bot 行为回归 | 高 | 本轮只新增 `services/system_module/` dry-run 骨架，不让现有 runtime 消费 |
| SystemModule 方案宣称“不兼容重做”，但仓库当前仍是 plugin/bus 架构 | 高 | 先做并行契约层和 compiler 前校验，不删除/替换 PluginBus |
| 模块 catalog 与 v2 draft 默认 skeleton 不一致 | 中 | S1' 只定义 canonical catalog；后续 S3'/S5' 再让 importer 产 9 默认模板并校验 |
| RuntimeStateBus 过早持久化引入存储迁移 | 中 | 首版只做内存实现 + TTL/scope/ownership 规则；persistent 仅保留 ttl 枚举 |
| 工作树已有无关改动 | 中 | 只触碰 Part B 新 package、tests、docs/log，不回滚 learning/frontend/.research |

**回滚方式**

- 删除 `services/system_module/` 与新增测试，移除本文 C0-C3 和迁移/维护日志条目即可；Part A importer 与 admin SPA 不受影响。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/system_module tests/test_system_module.py`
- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_system_module.py`
- 文档中 Part B 状态明确为 S1' dry-run 骨架，S2' 以后仍待后续。

**完成后回填**

- 实际改动：
  - 读取 `system-module-architecture.md` 完整 Part B 方案与 S1'~S12' 切片。
  - 核对现有 runtime：`PluginBus` 负责插件钩子，`PromptBuilder` 持有静态 identity/state_board，prompt 动态块由插件/Provider 注入。
  - 确认 S1' 采用并行 dry-run 骨架：新增 SystemModule 契约、RuntimeStateBus 和校验工具，不替换现有 PluginBus、不让 bot 消费 v2 persona。
- 验证证据：
  - 已读取 `kernel/types.py`、`kernel/bus.py`、`plugins/chat/plugin.py`、`services/llm/prompt_builder.py`、`services/block_trace/*`。
  - `rg -n "RuntimeStateBus|PromptBlock|PluginBus|prompt_builder" ...` 已定位当前主链路。
- 遗留风险：
  - Part B 仍是并行架构骨架，不具备正式运行时切流能力。
  - S2' 以后需要把 importer 默认模板从 3 份扩到 9 份，并让 S5' 校验消费 C1/C2 输出。

### C1 SystemModule 契约模型与默认目录

**开始前拆分**

1. 新建 `services/system_module/` 包，包含 `__init__.py`、`catalog.py`、`models.py`、`validator.py`、`state_bus.py`。
2. 在 `catalog.py` 定义 canonical module catalog：
   - 26 个首版模块；
   - 7 个 reserved 模块；
   - 6 个 required modules：`core.identity`、`core.guard`、`runtime.adapter`、`runtime.scheduler`、`memory.short_term`、`output.send`。
3. 在 `models.py` 定义 Part B S1' 最小 dataclass：
   - `ModuleContract`
   - `StateSlotDefinition`
   - `SwitchSurface`
   - `DisabledBehavior`
   - `ModuleGraphValidationResult`
4. 支持从 dict/YAML 风格 payload 解析 `ModuleContract`，不强制引入 JSON schema 依赖。
5. 在 `validator.py` 定义初版校验：
   - module id 必须合法；
   - required module 不能 disabled；
   - 每个 `state_consumes` 必须有 owner；
   - 每个 state slot 只能有一个 owner；
   - `depends_on` 必须存在；
   - DAG 无环；
   - 下游依赖被禁用时必须声明 `on_disabled`。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 一次性实现 26 个模块类导致假实现膨胀 | 高 | S1' 只定义 catalog/contract，不写模块业务类 |
| 过早绑定 YAML 物理格式导致后续 importer 难改 | 中 | `ModuleContract.from_dict()` 接受普通 dict，文件读取留到后续 S3'/S5' |
| DAG 校验过严卡住 reserved 模块 | 中 | reserved 默认 disabled，不参与 required；只在 enabled 时参与依赖校验 |
| 与现有 PluginBus 命名冲突 | 低 | 新包名 `services/system_module`，不 import kernel bus |

**回滚方式**

- 删除 `services/system_module/` 包和对应测试；不影响 Part A 与现有 runtime。

**验收证据**

- `tests/test_system_module.py` 覆盖 catalog 数量、required/reserved、contract parse、DAG missing owner / duplicate owner / cycle。

**完成后回填**

- 实际改动：
  - 新增 `services/system_module/__init__.py`。
  - 新增 `services/system_module/models.py`：定义 `ModuleContract`、`StateSlotDefinition`、`SwitchSurface`、`DisabledBehavior`、`Scope`、`SourceRef`、`ModuleIssue`、`ModuleGraphValidationResult`。
  - 新增 `services/system_module/catalog.py`：固化 27 个首版模块、7 个 reserved 模块、6 个 required 模块，并提供 `catalog_contracts()` / `catalog_system_modules()`。
  - 新增 `services/system_module/validator.py`：实现 module id、required enabled、reserved disabled、state owner、missing dependency、DAG cycle 等 dry-run 校验。
  - 对方案中的重复 owner 风险做了首版消歧：`runtime.adapter.send_receipt` 归 `output.send`，learning candidates 归 `memory.consolidator`。
- 验证证据：
  - 待 C3 统一用 `tests/test_system_module.py` 和 ruff 验证。
- 遗留风险：
  - 当前 catalog 是 canonical contract，不是模块业务实现；S6'~S9' 仍未启动。
  - `module.yaml` 物理文件读取和 JSON schema 校验未做，留到 S3'/S5'。

### C2 RuntimeStateBus 骨架与 dry-run 校验

**开始前拆分**

1. 在 `services/system_module/state_bus.py` 实现 in-memory `RuntimeStateBus`。
2. 构造时从 enabled `ModuleContract.state_owns` 建立 slot owner 表，重复 owner 直接 raise。
3. `set()` 强制：
   - slot 已注册；
   - `SourceRef.module_id` 等于 slot owner；
   - `SourceRef.evidence_path` 非空；
   - confidence 在 `[0, 1]`。
4. `get()` 按 slot TTL 与 `Scope` 取值，`per_turn/per_session/per_user/persistent` 使用不同 scope key。
5. `clear_per_turn()` 清理指定 turn 的 per-turn slots。
6. `snapshot_all_for_trace()` 输出 JSON-friendly snapshot，供后续 trace.yaml / BlockTrace 串联。
7. 增加测试覆盖 owner 拒绝、source/confidence 校验、per_turn 清理和 trace snapshot。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 内存 bus 被误认为正式持久化实现 | 中 | 文档和命名持续标注 S1' dry-run；persistent TTL 只保留 scope 语义 |
| Scope key 太复杂影响后续替换 | 中 | Scope 保持 dataclass，key 逻辑集中在 `Scope.key()` |
| `snapshot_all_for_trace()` 结构后续和 BlockTrace 不一致 | 低 | 先输出 JSON-friendly dict，S11'/trace 接入时再适配 |
| 同步 API 未来接异步 listener 不够 | 低 | S1' 不实现 subscribe；后续 runtime 接入再扩 |

**回滚方式**

- 删除 `state_bus.py` 和对应 tests，不影响 C1 contract/catalog。

**验收证据**

- `tests/test_system_module.py` 覆盖 RuntimeStateBus owner、TTL 和 trace snapshot。

**完成后回填**

- 实际改动：
  - 新增 `services/system_module/state_bus.py`：实现 in-memory `RuntimeStateBus`、`SlotSnapshot`、`StateSlotOwnershipError`。
  - `RuntimeStateBus` 构造时从 enabled module contracts 建立 slot owner 表，重复 owner 会拒绝。
  - `set()` 强制 owner 写入、`SourceRef.evidence_path` 非空、confidence 在 `[0,1]`。
  - `get()` 按 slot TTL 与 `Scope` 取值；`clear_per_turn()` 清理指定 turn 的 per-turn slot。
  - `snapshot_all_for_trace()` 输出 JSON-friendly snapshot，为后续 trace.yaml / BlockTrace 接入预留。
  - 新增 `tests/test_system_module.py`，覆盖 catalog、contract parse、DAG 校验、RuntimeStateBus owner/TTL/trace snapshot。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/system_module tests/test_system_module.py` 通过。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_system_module.py` 通过，`8 passed`。
- 遗留风险：
  - `RuntimeStateBus` 仍是同步内存实现；subscribe、持久化和真实 turn lifecycle 接入留给 S6'~S12'。
  - Part B 方案标题写“首版 26 个模块”，但表格实际列出 27 个首版模块 + 7 预留；本轮按表格事实固化为 34 个 catalog 条目，并记录为后续文档修正点。

### C3 S1' 验证、迁移清单与维护日志

**开始前拆分**

1. 运行 S1' 最小 lint/test：`services/system_module` 与 `tests/test_system_module.py`。
2. 运行 Part A persona importer/API 回归，确认新增 Part B 包不影响已有 importer/admin SPA 后端。
3. 更新 `docs/migrations/persona-v2-importer.md`：Part B 从“后续架构提案”改为“S1' dry-run 骨架已启动/已完成”，S2' 以后仍待后续。
4. 维护日志追加 Part B S1' 里程碑。
5. 更新本文 C3 完成回填与顶层状态。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 文档误读为 Part B runtime 已切流 | 高 | 明确写 S1' dry-run 骨架，不接入 chat/prompt runtime |
| 只跑 S1' 测试漏掉 Part A 回归 | 中 | 额外跑 `test_persona_importer.py` 与 `test_persona_importer_api.py` |
| 维护日志遗漏 26/27 计数差异 | 低 | 在日志与迁移清单中标出 canonical catalog 为 27+7 |

**回滚方式**

- 删除 `services/system_module/`、`tests/test_system_module.py` 与文档/维护日志条目即可。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/system_module tests/test_system_module.py services/persona tests/test_persona_importer.py tests/test_persona_importer_api.py`
- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_system_module.py tests/test_persona_importer.py tests/test_persona_importer_api.py`

**完成后回填**

- 实际改动：
  - 完成 S1' ruff/pytest 与 Part A importer/API 回归。
  - `docs/migrations/persona-v2-importer.md` 状态更新为 Part B S1' dry-run 骨架已完成，并新增 `## 5. Part B S1' dry-run 骨架迁移`。
  - `maintenance-log.md` 追加 `Persona Part B S1' SystemModule dry-run 骨架落地`。
  - 顶层状态更新为 `Part B S1' dry-run 骨架完成`。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/system_module tests/test_system_module.py` 通过。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_system_module.py` 通过，`8 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_system_module.py tests/test_persona_importer.py tests/test_persona_importer_api.py` 通过，`18 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/system_module tests/test_system_module.py services/persona tests/test_persona_importer.py tests/test_persona_importer_api.py` 通过。
- 遗留风险：
  - Part B S1' 只提供 contract/catalog/validator/state bus dry-run；正式 runtime 仍未切流。
  - S2' source.md 模板扩展、S3' 9 默认模板、S5' importer validator 接入、S6'~S9' 模块业务实现、S11' compiler 都未启动。
  - 现有工作树仍包含本阶段外 learning/frontend/static/.research 改动，本轮不接管也不回滚。

### C4 S2'/S3' source 扩展与 9 默认模板

**开始前拆分**

1. 扩展 `config/persona/_defaults/v2/`：
   - 新增 `runtime.yaml`、`state.yaml`、`thinker.yaml`、`adapter.yaml`、`capabilities.yaml`、`system.yaml`。
   - 更新 README，将默认模板范围从 3 份改为 9 份。
2. `system.yaml` 由 `services.system_module.catalog.catalog_system_modules()` 生成同构结构，避免文档/代码 drift。
3. `services/persona/builder.py`：
   - `DEFAULT_TEMPLATE_FILES` 从 3 份扩到 9 份。
   - 保留 15 draft 文件面；9 份有默认模板，其余 `relationships/memory` 等仍可 skeleton/抽取填充。
   - 从 source.md §12 模块开关抽取 checkbox，覆写 `system.yaml.modules.<id>.enabled`。
   - 从 source.md §11.2 `tone_palette` 抽取 voice/thinker tone 集合。
4. 更新 `_modules_readme()`，说明 modules 实例仍未生成，S1' 只提供 canonical catalog。
5. 补测试覆盖 9 默认模板落盘、system.yaml modules、source §12 禁用非 required 模块、禁用 required 模块产生 error。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 默认模板扩展导致 Part A 最小 source 现有测试失败 | 中 | 默认模板只补工程默认，不增加最小 source 必填项；旧测试应继续通过 |
| source §12 checkbox 语义误读 | 中 | 仅解析 `- [ ] module.id` 为 disabled，`- [x]` 或不出现保持默认 |
| 禁用 required module 被悄悄写入 draft | 高 | builder 先写入，再由 C5 validator 报 error；C4 可先做本地基础 error |
| 9 默认模板与 SystemModule catalog 漂移 | 中 | `system.yaml` 测试对齐 `catalog_system_modules()` |

**回滚方式**

- 删除新增默认模板，恢复 `DEFAULT_TEMPLATE_FILES` 与 README，移除新增 source 抽取逻辑和测试。

**验收证据**

- `tests/test_persona_importer.py` 覆盖 9 默认模板与 source §12/§11.2。

**完成后回填**

- 实际改动：
  - 新增 `config/persona/_defaults/v2/runtime.yaml`、`state.yaml`、`thinker.yaml`、`adapter.yaml`、`capabilities.yaml`、`system.yaml`。
  - 更新 `config/persona/_defaults/v2/README.md`，默认模板范围从 3 份扩到 9 份。
  - `services/persona/builder.py` 的 `DEFAULT_TEMPLATE_FILES` 扩到 9 份。
  - `system.yaml` 默认结构由 `services.system_module.catalog_system_modules()` 生成。
  - 新增 source §11.2 `tone_palette` 抽取，写入 `voice.yaml.tone_palette` 与 `thinker.yaml.policy.tone_set`。
  - 新增 source §12 模块开关 checkbox 抽取，写入 `system.yaml.modules.<id>.enabled`。
  - required module disabled / reserved module enabled 在 importer 阶段进入 error issue。
  - `_modules_readme()` 更新为 Part B S1' dry-run 语义。
- 验证证据：
  - `tests/test_persona_importer.py::test_persona_importer_loads_part_b_defaults_and_module_switches` 覆盖 9 默认模板、tone_palette、module switch 和 required/reserved issue。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_system_module.py` 通过，`16 passed`。
- 遗留风险：
  - source §13 模块定制 YAML patch 尚未实现；当前只做 §12 开关与 §11.2 tone 集。
  - 默认模板是 proposal-level dry-run，不代表正式 runtime 消费。

### C5 S5' SystemModule validator 接入 importer report

**开始前拆分**

1. 新增 `services/persona/system_validation.py`，把 `.draft/system.yaml` 与 canonical catalog 合并成 `ModuleContract` 列表。
2. 调用 `services.system_module.validate_module_graph()`，将 issues 写入 `_import_report.json.issues`。
3. 在 report fields 中加入 `_system_module_validation` 默认字段，便于 admin SPA 展示。
4. 保持 `strict=True` 行为：若 SystemModule validation 有 error，不写 `.draft/`。
5. 测试覆盖 required disabled、reserved enabled、默认 catalog ok。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| importer 和 system_module 双方循环 import | 中 | persona 只 import system_module public API，system_module 不 import persona |
| canonical catalog 和 system.yaml overrides 合并出错 | 中 | override 只允许改 `enabled/required/reserved`，contract 结构仍来自 catalog |
| error 太多影响 admin 阅读 | 低 | issue code/module_id/slot_id 明确，后续 UI 可筛选 |

**回滚方式**

- 移除 `system_validation.py`、builder 调用和测试；默认模板仍可保留。

**验收证据**

- `tests/test_persona_importer.py` 覆盖 validator issues。

**完成后回填**

- 实际改动：
  - 新增 `services/persona/system_validation.py`。
  - `validate_system_modules()` 将 draft `system.yaml.modules` 与 canonical catalog 合并为 `ModuleContract`，调用 `validate_module_graph()`。
  - SystemModule 校验结果写入 `_import_report.json.fields` 的 `_system_module_validation`。
  - 校验 issues 进入 `_import_report.json.issues`，因此 `strict=True` 会阻止写 `.draft/`。
  - `services/persona/builder.py` 在 Part B overrides 后调用 SystemModule validator。
- 验证证据：
  - `tests/test_persona_importer.py::test_strict_import_does_not_write_when_system_module_validation_fails` 覆盖 required disabled 时 strict 不写盘。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_system_module.py` 通过，`17 passed`。
- 遗留风险：
  - validator 当前基于 canonical catalog + enabled overrides，不读取真实 `modules/<id>/module.yaml` 文件；物理 manifest 校验留给后续模块实现切片。

### C6 S11' persona compiler dry-run

**开始前拆分**

1. 新增 `services/persona/compiler.py`，只做 dry-run，不写正式 runtime persona 路径。
2. 读取 `.draft/*.yaml` 与 `_import_report.json`，若 report status/error 或 SystemModule validation error 存在则拒绝。
3. 输出 `CompileResult`：
   - `ok`
   - `persona_id`
   - `prompt_blocks`（core.identity/core.voice/core.knowledge/core.examples/core.guard 的静态草案）
   - `module_order`
   - `warnings`
4. CLI 增加 `--compile-dry-run`，API 暂不扩展，避免前端新面过大。
5. 测试覆盖成功 dry-run 与有 error 时拒绝。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| dry-run 被误认为正式 compiler | 高 | 命名和返回 mode 均为 `dry_run`，不写 config/persona/<id> 正式运行时 |
| prompt block 草案与现有 PromptBuilder 格式冲突 | 中 | 只输出 JSON-friendly block，不接入 LLMClient |
| 编译范围过大 | 中 | 首版只编 core 静态 prompt blocks + module order，不编 runtime hooks |

**回滚方式**

- 删除 `compiler.py`、CLI 参数和测试；不影响 importer 写 draft。

**验收证据**

- `tests/test_persona_compiler.py` 覆盖 dry-run 编译。

**完成后回填**

- 实际改动：
  - 新增 `services/persona/compiler.py`，实现 `compile_persona_dry_run()`。
  - dry-run 读取 `.draft/*.yaml` 与 `_import_report.json`，report error 时拒绝。
  - 输出 `CompileResult`：`ok/mode/persona_id/prompt_blocks/module_order/warnings/errors`。
  - prompt block 草案覆盖 `core.identity`、`core.voice`、`core.knowledge`、`core.examples`、`core.guard`。
  - `services/persona/importer.py` 增加 CLI 参数 `--compile-dry-run`，仅输出 dry-run JSON，不写正式 runtime 路径。
  - 新增 `tests/test_persona_compiler.py`。
- 验证证据：
  - `tests/test_persona_compiler.py` 覆盖成功 dry-run、report error 拒绝、CLI `--compile-dry-run`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py` 通过，`20 passed`。
- 遗留风险：
  - compiler dry-run 只生成静态 core prompt blocks 与 module order，不编译 runtime hooks、state lifecycle 或正式 prompt injection。

### C7 Part B dry-run 闭环验证与收口

**开始前拆分**

1. 运行 ruff 覆盖 `services/system_module`、`services/persona`、相关 tests。
2. 运行 pytest 覆盖 system_module、persona importer/API、compiler。
3. 更新迁移清单：Part B S2'/S3'/S5'/S11' dry-run 完成，S6'~S12' runtime 切流仍未启动。
4. 更新维护日志。
5. 最终检查 git status，标注无关脏文件。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 本轮范围被误读为“Part B 全部结束” | 高 | 明确只是 dry-run 闭环结束，正式模块业务实现/切流未完成 |
| tests 只覆盖 happy path | 中 | 覆盖 required disabled、report error、compile reject |
| 文档状态与代码状态不一致 | 中 | 迁移清单和追踪总览同步更新 |

**回滚方式**

- 按 C4/C5/C6 分别 revert；S1' 可保留。

**验收证据**

- 待执行。

**完成后回填**

- 实际改动：
  - 完成 Part B dry-run 闭环：source 扩展/9 默认模板 -> SystemModule validation -> compiler dry-run。
  - 保持正式 runtime 不切流，现有 PluginBus / PromptBuilder / chat runtime 不接入 v2 persona。
  - 同步迁移清单与维护日志。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona services/system_module tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py` 通过。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py tests/test_persona_importer_api.py` 通过，`23 passed`。
- 遗留风险：
  - Part B “结束”限定为 dry-run 闭环；S6'~S9' 具体 SystemModule 业务实现、S12' feature flag 灰度切流仍未做。
  - admin SPA 尚未接 compiler dry-run 按钮/API。
  - source §13 模块定制 patch、真实 `modules/<id>/module.yaml` 文件生成/读取仍未做。
