# Persona Source Importer 整改与实施步骤

- **状态**：v0.4 实施版（2026-05-24，P0/P1/P2 + Part A S1-S5 已完成；S6/S10' 与 Part B 待后续）
- **上游来源**：[persona-source-importer.md §18 GPT 审计](./persona-source-importer.md#18-gpt-审计记录2026-05-24) + [§18 deepseek 独立审计](./persona-source-importer.md#18-deepseek-独立审计报告2026-05-24)
- **覆盖范围**：从两份审计的 5 项阻断 + 6 项条件项 + 4 项次要修正中收敛出可执行步骤；同时给出"§16 SystemModule 拆分方案"和"importer 首版（Part A）落地路径"
- **不含**：本文档**不**触发任何 `services/` / `plugins/` / `kernel/` 运行时改动；P0~P2 是文档层 + 配置层动作；S1' 之后的实现仍需用户显式发令
- **维护日志政策**：P0/P1/P2 收口已作为实施里程碑记入 `maintenance-log.md`；后续 P3+ 若触发 admin API、storage、运行时变更，仍需按 omubot-admin-console 政策补记

---

## 0. 阅读次序

1. **§1 总体策略** —— 一句话整改思路
2. **§2 整改交集表** —— 两份审计逐条对账，标记是否独立命中
3. **§3 P0 立即动作（文档/配置）** —— 解除阻断，必须先做完才能进 PR
4. **§4 P1 进 PR 前** —— 收敛 importer 首版合同
5. **§5 P2 实施阶段内嵌** —— 写代码时夹带的小修正
6. **§6 §16 拆分预案** —— SystemModule / RuntimeStateBus 单独建档
7. **§7 verify 矩阵** —— 每步的可观察证据
8. **§8 风险与回滚** —— D3/D4/D6 红线
9. **§9 实施日志** —— 每步落地后回填

---

## 1. 总体策略

把 `persona-source-importer.md` 当前版本拆成**两个独立文档**对应**两条独立路径**：

- **Part A · Source Importer**（本整改文档主轴）：`source.md → 多文件 draft + _import_report.json + CLI`，不依赖 RuntimeStateBus / SystemModule，可以独立按 S1'-S5' 切片落地
- **Part B · Runtime/SystemModule**（已拆至 [system-module-architecture.md](./system-module-architecture.md)）：26 模块 + RuntimeStateBus + SwitchMatrix + DAG + compiler，作为长远架构方向，不阻塞 Part A

整改不是推倒重来。两份审计都肯定了核心二分法（创作 6 / 工程 9）和 §15 的 21 注入源审计是高质量工作。需要做的是**把方案膨胀的部分从 importer 主轴里剥离**，并给每条决策标注证据来源。

---

## 2. 整改交集表

两份审计独立得出，但方向高度收敛。下表用作 P0~P2 优先级派生的依据。

| 序号 | 议题 | GPT 编号 | deepseek 编号 | 是否交集 | 严重度收敛 | 派生优先级 |
|---|---|---|---|---|---|---|
| 1 | `persona-spec-format.md` 未含 state/thinker/system | §18.2-1 | B1 | ✅ | 阻断 | P0 |
| 2 | `_defaults/v2/` 默认模板不存在 | （隐含 §18.4-3） | B2 | △ | 阻断 | P0 |
| 3 | §16 SystemModule 应拆出 | §18.2-3 | B3 | ✅ | 阻塞 | P0 |
| 4 | Freeze 语义自相矛盾（pending vs 正式） | §18.2-4 | （未显式） | ✗ | 阻塞 | P0 |
| 5 | 首版 draft 是 12 vs 15 文件不一致 | §18.2-2 | （C6 边角触及） | △ | 阻塞 | P0 |
| 6 | API 路径应挂 `/api/admin` | §18.3-1 | （未独立） | ✗ | 高风险 | P1 |
| 7 | LLM 模型硬编码 / 无 LLMTask | §18.3-2 | C2 | ✅ | 高风险 | P1 |
| 8 | draft YAML 内嵌元数据冲突正式 schema | §18.3-3 | （C4 退化行为相关） | △ | 高风险 | P1 |
| 9 | hard_rule 必须可机器化过严 | §18.3-4 | C5 | ✅ | 高风险 | P1 |
| 10 | §17 决策来源未标注 | （未显式） | B5 | ✗ | 阻塞 | P1 |
| 11 | 默认模板出处错引 `config/runtime.toml` | §18.4-1 | （未触及） | ✗ | 次要 | P2 |
| 12 | UI 已延后 S10' 但前文仍承诺 | §18.4-2 | （未触及） | ✗ | 次要 | P2 |
| 13 | `.gitignore` 缺 .draft / source.frozen.md | §18.4-3 | （未触及） | ✗ | 次要 | P2 |
| 14 | `source.md` 200 行模板入门门槛过高 | （未触及） | C1 | ✗ | 高 | P2 |
| 15 | "运维配置"应为创作/工程之外的第三类 | （未触及） | C3 | ✗ | 中 | P2 |
| 16 | 字段映射表缺"缺失时行为"列 | （未触及） | C4 | ✗ | 中 | P2 |
| 17 | v2 schema 标注 proposal-level | （未触及） | C6 | ✗ | 低 | P2 |
| 18 | 草案膨胀控制（已 1762 行） | （未触及） | §18.6 | ✗ | 工程纪律 | P2 |

**收敛结论**：

- 两审计**强交集**（都独立命中）4 项：1/3/7/9，权重最高
- 两审计**弱交集**（一方主提一方边角触及）3 项：2/5/8
- **GPT 独有**3 项：4 (Freeze 语义) / 6 (API 前缀) / 11 (config 出处) / 12 (UI 承诺) / 13 (gitignore)
- **deepseek 独有**6 项：10 (来源标注) / 14 (最小模板) / 15 (运维配置) / 16 (退化行为) / 17 (proposal-level) / 18 (膨胀控制)

每条独有项都被独立审计员当作"我看到的关键风险"提出，整改不应因为不交集就丢弃。

---

## 3. P0 立即动作（解除阻断）

> 触发条件：进入 P1 之前必须 100% 完成。所有 P0 动作都是**文档层或配置层**，不动 Python 代码。

### 3.1 P0-1：升级 `persona-spec-format.md`，新增 state/thinker/system 三文件 schema

**目标**：让方案 §16.8 引用的"15 文件 + `modules/`"在权威 spec 文档里有真实定义。

**动作**：

- 在 [docs/persona-spec-format.md](../persona-spec-format.md) 末尾追加章节 `## v2.1 扩展：runtime state / thinker / system`
- 每个新增文件给出最小 schema（先有键名 + 类型，详细字段表后续补）：
  - `state.yaml`：mood thresholds / schedule windows / calendar refresh / board snapshot ttl
  - `thinker.yaml`：决策原则 / 触发条件 / 输出 schema 引用
  - `system.yaml`：feature flags / per-group overrides 索引 / 模块开关矩阵
- 同时新增 `modules/<id>/module.yaml` 子目录约定：每个 module.yaml 含 `id` / `kind` / `inputs[]` / `outputs[]` / `persona_bindings` / `state_consumes` 五个键
- 保留原 12 文件章节作为"v2.0 minimal core"，新增章节标注 v2.1 扩展边界

**验证**：

```bash
grep -c '^## v2\.1\|state\.yaml\|thinker\.yaml\|system\.yaml' docs/persona-spec-format.md
# 期望：state/thinker/system 各 ≥3 次出现（章标题 + 字段表 + schema 块）
```

**回滚**：spec 章节是纯追加，不动 v2.0 内容；如要回滚 `git revert` 即可，importer 不读 spec 文件。

### 3.2 P0-2：建立 `config/persona/_defaults/v2/` 目录

**目标**：让 §8.1/8.2/8.3 的默认模板从"方案正文 YAML 块"变成真实文件。

**动作**：

```
config/persona/_defaults/v2/
├── guard.yaml      # hard_check / pattern_guards / soft_judge_thresholds 默认值
├── eval.yaml       # red_lines / golden_paths / regression_set 空骨架
├── trace.yaml      # sampling rate / pii_redaction / retention 默认
└── README.md       # "首版只覆盖工程型 3 文件，其余 9 工程文件由 importer 临时生成留空"
```

每份 YAML 内容直接抄方案正文 §8.1/8.2/8.3 的代码块；README.md 指明范围。

**验证**：

```bash
ls config/persona/_defaults/v2/
# 期望 4 个条目
test -s config/persona/_defaults/v2/guard.yaml && echo "guard ok"
```

**回滚**：纯新增配置文件，无运行时副作用（importer 未实施）。`git rm -r config/persona/_defaults/v2/` 即可。

### 3.3 P0-3：拆分 §16，建立 `system-module-architecture.md`

**目标**：让 importer 不再背负 26 模块 / RuntimeStateBus / DAG / compiler 的依赖。

**动作**：

- 新建 [docs/tracking/system-module-architecture.md](./system-module-architecture.md)
- 把 `persona-source-importer.md §16.1~§16.12` **整段剪切**过去（注意是剪切不是复制；同段不能两边都在）
- 新文档结构：
  - §0 定位（独立 RFC，不绑 importer）
  - §1~§N 抄过来的 §16 子节
  - §X 与 importer 的接口（importer 输出 draft，本文档定义运行时如何消费）
- 在 importer 文档原 §16 位置只留**单段引用块**：

  > §16 SystemModule / RuntimeStateBus / SwitchMatrix / DAG 已拆出，见 [system-module-architecture.md](./system-module-architecture.md)。importer 首版（Part A）不依赖该提案。

- 同步删 importer 文档中所有"§16.X 决定 importer 应如何抽取 mood 阈值"等强耦合段落，改为"先按 spec 字段抽取，runtime 消费由 Part B 决定"

**验证**：

```bash
wc -l docs/tracking/persona-source-importer.md docs/tracking/system-module-architecture.md
# 期望：importer 文档行数显著下降；架构文档新建后 ≥ 200 行
grep -c '^## 16\.' docs/tracking/persona-source-importer.md
# 期望 0
```

**回滚**：拆分后两文档独立 commit，回滚 1 个 commit 即恢复 §16；不影响 §0~§15、§17~§18。

### 3.4 P0-4：统一 Freeze 语义为 `Pending Freeze`

**目标**：消除 GPT §18.2-4 指出的"UI 章节说 Freeze 写正式路径，风险章节又说只落 _pending_freeze"自相矛盾。

**动作**：

- 在 importer 文档 §0 加一行明确："compiler 落地前，Freeze 仅写 `_pending_freeze/`，不写 `config/persona/<id>/`"
- 替换全文所有"Freeze 后写入 config/persona/"为"Pending Freeze 后写入 _pending_freeze/"
- 新增 §X "Schema Freeze vs Pending Freeze" 一节，明确两者条件：
  - **Pending Freeze**：v2 spec 已冻结但 compiler 未上线 → `_pending_freeze/<id>/`
  - **Schema Freeze**：compiler dry-run 通过 → `config/persona/<id>/`（首版不实现）

**验证**：

```bash
grep -nE 'Freeze 后(写入|写到)|Freeze 会把.*拷' docs/tracking/persona-source-importer.md
# 期望：所有命中行都已加 _pending_freeze 限定词
```

### 3.5 P0-5：明确首版输出是 15 文件 skeleton（partial draft）

**目标**：消除 GPT §18.2-2 指出的"流水线图写 12 个 .draft / Q8 又说默认模板只覆盖 3 个"自相矛盾。

**动作**：

- importer 文档 §5 流水线图改为："首版输出 = 15 文件 skeleton + `modules/<id>/module.yaml` 占位"
- 表格列出 15 文件中：
  - **6 创作型**：由 LLM 抽取填充字段（带 source_span / confidence）
  - **3 默认覆盖**（guard/eval/trace）：从 `_defaults/v2/` 复制
  - **6 工程型剩余**（runtime/capabilities/adapter/state/thinker/system）：生成空骨架（仅顶层键，注释标 `TODO: 由 P3 admin SPA 配置`）
- `modules/` 子目录首版生成空目录 + `_README.md`，不创建 module.yaml 实例

**验证**：手工核 §5 流水线图与 §6 字段映射表是否一致；与 P0-2 创建的 `_defaults/v2/` 内容对应。

---

## 4. P1 进 PR 前（收敛 importer 首版合同）

> 触发条件：P0 全部完成后启动；本节产物是"S1' 进 PR 前最后一次审定"。

### 4.1 P1-1：API 前缀对齐 `/api/admin/persona/*`

- 替换 importer 文档所有 `/api/persona/import` 为 `/api/admin/persona/import`
- 同样处理 `draft` / `freeze` / `report` 几个端点
- 与 [admin/routes/api/__init__.py:51](../../admin/routes/api/__init__.py#L51) `APIRouter(prefix="/api/admin")` 对齐
- 在文档 §X "API 契约" 节加一行："所有 importer 端点经 `admin/routes/api/persona_importer.py` 注册到 `/api/admin/persona/*`"

### 4.2 P1-2：新增 `persona_import` LLMTask profile

- importer 文档 §5.2 删除"硬编码 `claude-haiku-4-5`"措辞
- 改为："importer 调用 `LLMClient.chat(task='persona_import', ...)`，模型由 `BotConfig.llm.tasks.persona_import.model` 决定，默认 `claude-haiku-4-5`，可通过 `PERSONA_IMPORTER_LLM_MODEL` env override"
- 配置层动作（仍属文档示意，不动代码）：在 importer 文档 §X 给出 BotConfig profile 片段范本

### 4.3 P1-3：draft 元数据外挂到 `_import_report.json`

- importer 文档 §X "draft schema" 节明确：
  - `<id>-v2/.draft/*.yaml` 写**纯净 v2 schema**（标量/数组，不含 source_span / confidence / extractor）
  - `<id>-v2/.draft/_import_report.json` 写**逐字段元数据**：`{file, key_path, source_span, confidence, extractor, default_used}`
- admin SPA 高亮逻辑（S10'）从 `_import_report.json` 读，不从 YAML 读
- 这条修改的好处是 draft 文件可以直接被 v2 compiler 当 input 试跑（compiler 不需要懂 source_span）

### 4.4 P1-4：hard_rule 三类拆分

- 把 §9 不变量 "hard_rule 必须可机器化" 拆为三类：
  - **`pattern_guardable`**：可映射为 regex / pattern → guard.yaml.hard_check
  - **`judge_guardable`**：需 soft_judge LLM 验证 → guard.yaml.soft_judge
  - **`eval_only`**：仅在 eval 阶段验证（如"语气是否过冷"），不进 guard
- importer 校验时按类做，不再要求所有 hard_rule 都进 hard_check
- §7.2 校验规则改写为："每条 hard_rule 必须显式标注 enforce 类（pattern/judge/eval），且各类下游目标存在"

### 4.5 P1-5：§17 决策表加"输入来源"列

- §17.1 决策表新增一列 `输入来源`，值域：
  - `用户选择`（Q1~Q6 已确认）
  - `方案推荐 + 待外部审计`（Q7~Q17，未经独立审计）
  - `审计交集`（被 GPT/deepseek 共同建议）
- 在表头注释明确：未经审计的"方案推荐"在进 PR 前需要至少一次外部审计或用户复核

### 4.6 P1-6：§16 拆出后，importer 文档同步收口

P0-3 拆出 §16 后，importer 文档需要同步做以下小修：

- §3 创作 vs 工程 二分类不再扩展为三分类（C3 改为 P2 处理）
- §15 注入源全表保留，但每条标注"是否进 importer 抽取 / 是否进 Part B 模块"
- §17.4 把"S1' 实现需用户显式发令"改为"S1' 启动需 P1 全部完成"

---

## 5. P2 实施阶段内嵌（写代码时夹带）

> 触发条件：S1' 已启动，PR 已开；P2 不阻断 PR 上线，但每条都要在对应 PR 的 verify 矩阵里勾选完成。

### 5.1 P2-1：默认模板出处更正

- importer 文档所有提到 "config/runtime.toml" 的位置改为 "kernel/config.py + config/config.json"

### 5.2 P2-2：删除已延后的 UI 承诺

- §X "首版交付" 节明确："首版仅 CLI + JSON report，admin SPA 双栏高亮 / Freeze 按钮在 S10' 落地"

### 5.3 P2-3：补 `.gitignore` 物理护栏（D7）

- 在仓库 `.gitignore` 末尾追加：

  ```
  config/persona/*/.draft/
  config/persona/*/source.frozen.md
  config/persona/*/_pending_freeze/
  ```

- 这条动作在 S1' 第一次 importer 写盘前必须完成，否则 draft 会被误 commit

### 5.4 P2-4：新增"最小 source.md"模板

- 在 importer 文档 §4 加 §4.4 "最小模板（30 行）"
- 仅覆盖 persona / voice / knowledge 三段必填；其余九段标 `（可选，留空走默认）`
- 完整模板（§4.2 当前版本）改为"进阶模板"，从首屏移到附录

### 5.5 P2-5：新增"运维配置"第三类

- §3 二分类扩为三分类：
  - 创作型 6（不变）
  - 工程型默认 3（guard/eval/trace）
  - 工程型推导 6（runtime/capabilities/adapter/state/thinker/system，默认空骨架，由 admin SPA 配置）
  - **运维配置 N**（context_retrieval RRF / packing prompt-injection guard / per-group at_only / sticker_mode 等）：不进 source.md，仅在 admin SPA 配置面板出现

### 5.6 P2-6：字段映射表新增"缺失时行为"列

- §6 字段映射表第 5 列加 `缺失时行为`：值域 `silent_default` / `warn_default` / `error`
- 必填字段缺失 = error；选填带默认 = silent_default；选填无默认 = warn_default

### 5.7 P2-7：v2 schema 标注 proposal-level

- 文档头部加："本方案对 v2 schema 的描述是 proposal-level，最终以 `persona-spec-format.md` v2.1+ 为准。importer 用松 schema（unknown keys allowed）实现，不与 spec 紧耦合迭代。"

### 5.8 P2-8：膨胀控制条款（工程纪律）

- 文档末尾加一节 §X "维护纪律"：
  - 每次修订删一段可推迟的内容，再追加新内容
  - 行数硬上限：importer 文档 ≤ 1500 行，超过须拆分
  - 每个 P3 切片完成后回收一次（合并/精简过时段）

---

## 6. §16 拆分预案（详）

### 6.1 拆分文件清单

| 源段（importer §16.X） | 目标位置（system-module-architecture.md） | 动作 |
|---|---|---|
| §16.1 SystemModule 定义 | §1 SystemModule 抽象 | 整段移 |
| §16.2 RuntimeStateBus 接口 | §2 RuntimeStateBus | 整段移 |
| §16.3 SwitchMatrix 三层 | §3 SwitchMatrix | 整段移 |
| §16.4 DAG 编译期校验 | §4 DAG | 整段移 |
| §16.5 26 一级模块清单 | §5 模块清单 | 整段移 |
| §16.6 7 预留扩展槽 | §6 预留槽 | 整段移 |
| §16.7 模块生命周期 | §7 生命周期 | 整段移 |
| §16.8 文件结构 15 + modules/ | §8 文件结构 | 整段移；importer 文档保留引用 |
| §16.9 注入源到 schema 的 mapping | §9 注入源 → schema | 整段移；importer §15 保留"是否进 importer"标注 |
| §16.10 灰度切流 | §10 灰度 | 整段移 |
| §16.11 实施切片（services/system_module/） | §11 实施切片 | 整段移 |
| §16.12 衍生开放问题（Q13-Q17） | §12 开放问题 | 整段移；importer §17.1 决策表 Q13-Q17 同步迁移或加引用 |

### 6.2 双向引用约定

- importer 文档 §16 位置：单段引用块 + "Part A 不依赖 Part B"声明
- 架构文档 §0 位置：单段引用块 + "input 由 importer 输出，consumer 由 compiler / module runner 实现"
- §17 决策表 Q13-Q17（涉及 SystemModule 的）：标注 `归属 = Part B`，importer 不再以此为合同

### 6.3 拆分后 importer 行数预期

- 当前 1762 行（含两份审计）
- 移走 §16 ~570 行 + §16 衍生段落 ~80 行
- 加上整改文档的引用 + Pending Freeze 章节 ~50 行
- 预期 ≤ 1200 行，符合 §5.8 膨胀控制硬上限

---

## 7. Verify 矩阵

每个 P 步骤的可观察证据（D4 完成声明含证据）。

### 7.1 P0 verify

| 步骤 | 验证命令 / 证据 |
|---|---|
| P0-1 spec 升级 | `grep -c 'state\.yaml\|thinker\.yaml\|system\.yaml' docs/persona-spec-format.md` ≥ 9 |
| P0-2 默认模板 | `ls config/persona/_defaults/v2/` 含 4 文件；`yamllint config/persona/_defaults/v2/*.yaml` 通过 |
| P0-3 §16 拆出 | `grep -c '^## 16\.' docs/tracking/persona-source-importer.md` = 0；`wc -l docs/tracking/system-module-architecture.md` ≥ 200 |
| P0-4 Pending Freeze | `grep -nE 'Freeze 后(写入\|写到)' docs/tracking/persona-source-importer.md` 全部含 `_pending_freeze` |
| P0-5 首版 15 skeleton | §5 流水线图 + §6 字段映射表手工对账，§5 列出 15 个 .draft 文件 |

### 7.2 P1 verify

| 步骤 | 验证命令 / 证据 |
|---|---|
| P1-1 API 前缀 | `grep -nE '/api/persona/' docs/tracking/persona-source-importer.md` 全部已加 `admin/` |
| P1-2 LLMTask | importer 文档 §5.2 含 `task='persona_import'`；BotConfig profile 片段已在文档示意 |
| P1-3 draft 元数据外挂 | §X draft schema 节明确两份产物的字段差异 |
| P1-4 hard_rule 三类 | §9 不变量 + §7.2 校验规则同步更新 |
| P1-5 §17 来源标注 | §17.1 决策表第 5 列 `输入来源` 17 行全部填写 |
| P1-6 importer 同步收口 | §3 / §15 / §17.4 三节按 §16 拆出后的边界改写 |

### 7.3 P2 verify

每条 P2 在对应 PR 的 verify 矩阵勾选；PR 描述末尾贴 `P2-X 已完成` 行。

---

## 8. 风险与回滚

### 8.1 文档层风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| §16 拆分时漏移段落或交叉引用断 | 中 | 拆分前 `git grep "§16\."` 列出全部反向引用，逐条改写；拆分后 grep 应返回 0 |
| `persona-spec-format.md` v2.1 章节字段与 importer §6 映射表不同步 | 中 | P0-1 完成后立刻跑 P0-5 对账；两文档同时改 |
| Pending Freeze 路径与 v2 compiler 后期路径冲突 | 低 | §X "Schema Freeze vs Pending Freeze" 一节预留 compiler 接入点 |
| 整改文档自身膨胀 | 中 | 本文档行数硬上限 600 行；超过须拆 |

### 8.2 配置层风险（P0-2 / P2-3）

| 风险 | 概率 | 缓解 |
|---|---|---|
| `_defaults/v2/*.yaml` 字段与未来 spec v2.1 不一致 | 中 | 默认模板标 `# v2.1-proposal` 注释；spec 升 v2.2 时同步 review |
| `.gitignore` 误命中已 commit 的 `.draft/` 文件 | 低 | D7：先 `git check-ignore -v config/persona/*/.draft/` 确认无现存追踪 |

### 8.3 D 系列条款交叉

- **D1 同模式扫描**：每条 P0/P1 改文档时，`grep` 全文同模式位点（如改 API 前缀要全替不能漏）
- **D3 迁移清单**：§16 拆分等价于"旧→新文件 / 路由"两列，建表存入 `docs/migrations/persona-v2-importer.md`
- **D4 完成声明含证据**：每个 P 步骤回填 §9 实施日志时必须贴 verify 命令输出
- **D6 admin/static 不需 rebuild**：本整改不动前端，与 D6 无关
- **D7 git hygiene**：P2-3 `.gitignore` 修改前必跑 `git stash list && git status -uno`

---

## 9. 实施日志

每完成一步立刻回填。

| 时间 | 步骤 | 状态 | commit | 证据 |
|---|---|---|---|---|
| 2026-05-24 | 整改文档建档 | ✅ 落地 | （未 commit） | 本文件创建于 [docs/tracking/persona-source-importer-remediation.md](./persona-source-importer-remediation.md) |
| 2026-05-24 | P0-1 spec 升级 | ✅ 完成 | （未 commit） | `persona-spec-format.md` 增加 v2.1 state/thinker/system/modules 扩展 |
| 2026-05-24 | P0-2 默认模板目录 | ✅ 完成 | （未 commit） | `config/persona/_defaults/v2/` 含 README/guard/eval/trace；`.gitignore` 放行模板但保护真实配置 |
| 2026-05-24 | P0-3 §16 拆出 | ✅ 完成 | （未 commit） | Runtime/SystemModule 已拆至 [system-module-architecture.md](./system-module-architecture.md)，importer §16 只保留引用 |
| 2026-05-24 | P0-4 Pending Freeze | ✅ 完成 | （未 commit） | compiler 前 freeze 统一为 `_pending_freeze/`；Schema Freeze 需 compiler dry-run 后再写正式路径 |
| 2026-05-24 | P0-5 首版 15 skeleton | ✅ 完成 | （未 commit） | §5.1 明确 15 YAML partial skeleton + `modules/_README.md` + `_import_report.json` |
| 2026-05-24 | P1-1~P1-6 importer 合同收敛 | ✅ 完成 | （未 commit） | API 前缀、`persona_import` task profile、report 外挂、hard_rule enforce 分类、§17 来源列、Part A/Part B 边界均已同步 |
| 2026-05-24 | P2-1~P2-8 工程纪律与边角收口 | ✅ 完成 | （未 commit） | 默认模板出处、UI 后移、最小模板、运维配置、缺失行为、proposal schema、膨胀控制均已落地；详见 [执行追踪](./persona-source-importer-remediation-execution.md) |
| 2026-05-24 | Part A S1-S5 后端/CLI 首版 | ✅ 完成 | （未 commit） | `services/persona` importer、`persona_import` LLMTask、`/api/admin/persona/*`、CLI 与测试已落地；详见 [执行追踪](./persona-source-importer-remediation-execution.md) §5 |

---

## 10. 与原文档的关系

本整改文档**不替代** [persona-source-importer.md](./persona-source-importer.md)，而是它的"整改 + 实施步骤"伴生文档。原文档的角色是：

- v0.2 草案 + 21 注入源审计 + §17 决策表 + §18 两份审计 → **设计与审计的归档**
- 进入实施阶段后，原文档每个 P/A 步骤完成时同步收口（按 §4.6 / §6 拆分预案）
- 当前原文档在 P0~P2 + Part A S1-S5 完成后，停在 v0.4 状态作为 importer 设计与首版实现参考

本文档（remediation）的角色：

- 把审计结论翻译成可执行步骤
- 跟踪每步的实际落地证据
- S1' 启动后，本文档接管"实施前置"的责任，原文档退出主轴
